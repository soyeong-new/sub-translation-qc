"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from app.providers.base import ModelProvider
from app.core.ingest import load_srt, extract_audio, generate_video_proxy, split_audio_into_chunks
from app.core.pronoun_hints import find_pronoun_hint
from app.core.alignment import align
from app.core.format_rules import check_line_length, check_ellipsis, MAX_LINE_CHARS, MAX_LINES
from app.core.safety_net import shrink_violating_lines
from app.language_profiles.loader import load_profile
from app.knowledge.loader import (
    load_knowledge, load_sensitive_terms, load_glossary, load_cta_patterns,
    load_profanity_dictionary,
)
from app.core.scene_splitting import split_into_scenes
from app.core.anchor_matching import find_anchor_candidates, find_relationship_anchor_candidates
from app.core.pretreatment import run_pretreatment
from app.core.diffing import findings_from_corrections, apply_corrections
from app.schemas import SegmentText

logger = logging.getLogger(__name__)

# check_grammar_necessity는 입력 줄 하나당 결과 객체 하나를 빠짐없이 반환해야 하는
# 스키마라(correct_primary처럼 바뀐 줄만 sparse하게 돌려주는 게 아님), 에피소드
# 전체를 한 번에 보내면 max_tokens(4096)을 넘겨 응답이 JSON 중간에서 잘리고
# 파싱이 통째로 실패한다 — 이러면 성별/격식 체크가 에피소드 전체에서 조용히
# 사라진다. 실제 에피소드 길이(보통 수백 줄)에서도 한 번의 호출이 넘치지 않도록
# 배치로 나눠 보낸다.
GRAMMAR_NECESSITY_BATCH_SIZE = 100

# gpt-4o-mini-transcribe(OpenAI 오디오 API)는 25MB/약 1500초(25분) 제한이
# 있다. 16kHz mono 16bit PCM WAV는 초당 32KB라 25MB는 약 781초(≈13분)에
# 해당하므로, 이보다 여유 있게 낮춰서 잡는다. split_audio_into_chunks의
# 기본값과 같은 값이지만, 병합 시 오프셋 계산에 이 상수를 그대로 다시
# 써야 하므로 여기서도 명시적으로 갖고 있는다.
STT_CHUNK_SECONDS = 600.0


def _offset_segments(segments: list, offset_seconds: float) -> list:
    """STT 조각 결과의 타임코드(그 조각 파일 안에서 0초부터 시작하는 상대
    시각)를 에피소드 전체 기준 절대 시각으로 보정한다."""
    return [
        {**s, "start": s["start"] + offset_seconds, "end": s["end"] + offset_seconds}
        for s in segments
    ]


async def _transcribe_in_chunks(provider: ModelProvider, wav_path: str) -> list:
    """긴 오디오를 여러 조각으로 나눠 각각 STT한 뒤 이어붙인다.
    asyncio.gather는 완료 순서와 무관하게 항상 입력 순서대로 결과를
    반환하므로(공식 보장 사항), 조각을 시간 순서대로 넘기기만 하면 병렬로
    돌려도 최종 결과 순서가 흐트러지지 않는다. split_audio_into_chunks가
    분할 없이 원본 경로를 그대로 반환했을 수 있으므로(짧은 오디오),
    정리(unlink) 시 원본 wav_path는 절대 지우지 않는다 — 그건 호출자의
    finally 블록이 이미 담당한다."""
    chunk_paths = await asyncio.to_thread(
        split_audio_into_chunks, wav_path, STT_CHUNK_SECONDS)
    try:
        chunk_results = await asyncio.gather(
            *(provider.transcribe(p) for p in chunk_paths)
        )
    finally:
        for p in chunk_paths:
            if p != wav_path:
                Path(p).unlink(missing_ok=True)
    merged: list = []
    for i, segments in enumerate(chunk_results):
        merged.extend(_offset_segments(segments, i * STT_CHUNK_SECONDS))
    return merged


async def run_pipeline(video_path: str, target_srt_path: str,
                        language: str, variant: str, target_version_id: str,
                        provider: ModelProvider,
                        prior_characters: Optional[list] = None,
                        prior_relationships: Optional[list] = None,
                        cached_korean_segments: Optional[list] = None,
                        cached_video_proxy_path: Optional[str] = None,
                        english_srt_path: Optional[str] = None) -> dict:
    """design §전체 파이프라인의 오케스트레이터. S1(사전/규칙) → S2(Claude 1차)
    → S3(GPT 2차) → S4(최종 안전망) 순서로 순차 실행하며, 각 단계의 diff가
    findings가 된다. 오디오/영상 프록시는 STT 직후 한 번만 생성한다.

    원본 영상 삭제는 여기서 하지 않는다 — 이 함수가 반환한 뒤에도 아직 DB에
    아무것도 영속화되지 않은 상태이므로, 여기서 지우면 프로세스가 이 함수와
    호출자(background.py)의 저장 사이 어딘가에서 죽었을 때 원본도 없고 결과도
    없는 상태가 된다. 호출자가 결과를 실제로 커밋한 뒤에 지우도록
    `video_path`를 결과에 그대로 담아 돌려준다."""
    warnings: list = []
    target_segments = load_srt(target_srt_path)

    english_segments: list = []
    if english_srt_path:
        try:
            english_segments = load_srt(english_srt_path)
        except Exception as exc:
            logger.exception(
                "영어 SRT 파싱 실패, 대명사 힌트 생략하고 계속 진행 (target_version_id=%s)",
                target_version_id)
            warnings.append({"stage": "영어 SRT 대조", "message": str(exc)})

    if cached_korean_segments is not None and cached_video_proxy_path is not None:
        # Episode 단위 캐시 재사용 — 같은 화를 다른 대상언어로 다시 분석하거나
        # 재시도할 때, 이미 든 STT 비용을 또 쓰지 않는다. 최초 성공 후에는
        # 원본 영상이 삭제되므로(background.py), 캐시가 없으면 재시도 자체가
        # 불가능해질 수 있다는 점에서도 중요하다.
        korean_raw = cached_korean_segments
        video_proxy_path = cached_video_proxy_path
    else:
        # extract_audio는 subprocess.run(check=True)로 ffmpeg을 동기 호출하는,
        # 잠재적으로 몇 분씩 걸리는 CPU 바운드 작업이다. asyncio.to_thread로 감싸지
        # 않으면 이 코루틴이 FastAPI 프로세스의 이벤트 루프 자체를 막아버려 헬스체크나
        # 다른 요청까지 전부 멈춘다 — 아래 generate_video_proxy와 동일한 이유.
        wav_path = await asyncio.to_thread(extract_audio, video_path)
        try:
            # STT(네트워크 호출)와 영상 저화질 프록시 생성(로컬 ffmpeg, CPU 바운드)은
            # 서로 결과를 주고받지 않는 독립적인 작업이라 동시에 실행한다 — STT를
            # 기다리는 동안 영상 트랜스코딩도 같이 진행되어 전체 대기 시간이 줄어든다.
            # generate_video_proxy는 동기 함수라 asyncio.to_thread로 감싸 이벤트
            # 루프를 막지 않게 한다.
            #
            # return_exceptions=True로 두 작업의 완료를 항상 기다린다 — to_thread로
            # 감싼 동기 함수는 취소할 수 없으므로, transcribe가 먼저 실패해도
            # generate_video_proxy는 백그라운드 스레드에서 계속 돌아 프록시 파일을
            # 만들어낼 수 있다. 예외를 즉시 전파(gather 기본 동작)하면 그 파일이
            # 아무도 참조하지 않는 고아로 남는다 — 아래에서 결과를 직접 검사해
            # 그런 파일이 생겼으면 지우고 나서 실패를 전파한다.
            korean_raw, video_proxy_path = await asyncio.gather(
                _transcribe_in_chunks(provider, wav_path),
                asyncio.to_thread(generate_video_proxy, video_path),
                return_exceptions=True,
            )
        finally:
            # STT가 WAV를 다 읽은 뒤(성공/실패 무관)에는 더 이상 필요 없다. 2시간
            # 분량 영화의 16kHz mono WAV는 ~230MB에 달해, 지우지 않으면 영상
            # 프록시 기능의 스토리지 절감 취지가 무색해진다.
            Path(wav_path).unlink(missing_ok=True)

        if isinstance(korean_raw, Exception) or isinstance(video_proxy_path, Exception):
            if not isinstance(video_proxy_path, Exception) and video_proxy_path:
                Path(video_proxy_path).unlink(missing_ok=True)
            raise korean_raw if isinstance(korean_raw, Exception) else video_proxy_path

    korean_segments = [SegmentText(**s) for s in korean_raw]

    pairs = align(korean_segments, target_segments)

    # 온점 자동보정은 다른 모든 단계보다 먼저 적용한다 — 이후 단계가 보정된
    # 텍스트를 기준으로 작업하도록.
    ellipsis_violations = check_ellipsis(pairs)
    fixed_by_segment = {v.segment_id: v.fixed_text for v in ellipsis_violations}
    for pair in pairs:
        if pair.id in fixed_by_segment:
            pair.target.text = fixed_by_segment[pair.id]

    profile = load_profile(language, variant)
    knowledge = load_knowledge()
    sensitive_terms = load_sensitive_terms()
    glossary = load_glossary()
    cta_patterns = load_cta_patterns()
    profanity_dictionary = load_profanity_dictionary()

    pretreatment = run_pretreatment(
        pairs, glossary, cta_patterns, profanity_dictionary, sensitive_terms,
        target_version_id,
    )
    pairs = pretreatment.pairs

    # GPT 2차의 "원본 대조 안전장치"용으로, 사전필터(CTA 삭제/비속어 치환 등
    # 정책적 편집)까지 전부 적용된 뒤의 텍스트를 원본으로 기록해 둔다. 사전필터
    # 이전 값을 쓰면 GPT가 "1차 교정자가 뭔가 잘못 고쳤나" 대조하다가 정책적으로
    # 이미 삭제/치환된 내용을 도로 복원하도록 유도될 수 있다 — 사전필터는 제안이
    # 아니라 정책이므로 GPT의 대조 대상이 되면 안 된다.
    original_target_by_id = {
        p.id: p.target.text for p in pairs if p.target is not None
    }

    # 인물/관계 로스터는 이제 title에 이미 있는 Character/Relationship에서
    # 그대로 받아온다(prior_characters/prior_relationships) — 매 실행마다
    # LLM으로 처음부터 다시 추론하지 않는다. 아무것도 안 넘어오면(아직 로스터가
    # 없는 title) 빈 목록으로 시작한다.
    characters = prior_characters if prior_characters is not None else []
    relationships = prior_relationships if prior_relationships is not None else []

    # 문법 필요성 판단(줄 단위, LLM): 성별/격식 판단이 실제로 필요한 줄만
    # 골라낸다. 걸리지 않은 줄은 이후 앵커 매칭·사람 리뷰에 전혀 들어가지 않는다.
    grammar_pairs = [
        {"id": p.id, "target_text": p.target.text if p.target else ""}
        for p in pairs if p.target is not None
    ]
    segment_resolutions: list = []
    try:
        batches = [
            grammar_pairs[i:i + GRAMMAR_NECESSITY_BATCH_SIZE]
            for i in range(0, len(grammar_pairs), GRAMMAR_NECESSITY_BATCH_SIZE)
        ]
        # 배치들은 서로 완전히 독립적인 호출이라(각 줄을 그 줄 텍스트만 보고
        # 판단) 동시에 돌려도 안전하다 — 이 코드베이스가 이미 correct_primary/
        # verify_and_refine 등 독립 LLM 호출에 asyncio.gather를 쓰는 것과 동일한
        # 패턴. 배치 중 하나라도 예외를 던지면 gather가 그대로 전파해 바깥
        # try/except가 기존처럼 경고로 변환하고 나머지 파이프라인은 계속 진행한다.
        batch_results = await asyncio.gather(
            *(provider.check_grammar_necessity(batch, profile) for batch in batches)
        )
        grammar_flags = [flag for batch_result in batch_results for flag in batch_result]
        flags_by_id = {f["id"]: f for f in grammar_flags}
        flagged_pairs = [
            p for p in pairs
            if flags_by_id.get(p.id, {}).get("gender_check_needed")
            or flags_by_id.get(p.id, {}).get("formality_check_needed")
        ]
        if flagged_pairs:
            scenes = split_into_scenes(pairs)
            scene_by_pair_id = {}
            for scene in scenes:
                for scene_pair in scene:
                    scene_by_pair_id[scene_pair.id] = scene
            for p in flagged_pairs:
                flags = flags_by_id[p.id]
                scene = scene_by_pair_id.get(p.id, [p])
                gender_candidates = find_anchor_candidates(scene, characters) if characters else []
                formality_candidates = (
                    find_relationship_anchor_candidates(scene, relationships) if relationships else []
                )
                gender_needed = bool(flags.get("gender_check_needed"))
                english_hint = (
                    find_pronoun_hint(p.target.start, p.target.end, english_segments)
                    if gender_needed and english_segments and p.target is not None
                    else None
                )
                segment_resolutions.append({
                    "segment_id": p.id,
                    "gender_check_needed": gender_needed,
                    "formality_check_needed": bool(flags.get("formality_check_needed")),
                    "gender_anchor_candidates": gender_candidates if gender_needed else [],
                    "formality_anchor_candidates": (
                        formality_candidates if flags.get("formality_check_needed") else []
                    ),
                    "english_pronoun_hint": english_hint,
                })
    except Exception as exc:
        logger.exception(
            "문법 필요성 판단 실패, 성별/격식 체크 생략하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        warnings.append({"stage": "문법 필요성 판단", "message": str(exc)})

    format_constraint = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."

    claude_pairs = [
        {"id": p.id, "korean_text": p.korean.text if p.korean else "",
         "target_text": p.target.text if p.target else ""}
        for p in pairs if p.target is not None
    ]
    # design §에러 처리: Claude/GPT 패스 중 하나가 실패해도 전체 분석을 실패
    # 처리하지 않는다 — 앞단계 비용(STT 등)이 이미 들었으므로, 실패한 패스만
    # 스킵하고 이전 단계 결과 그대로 다음으로 진행한다(부분 실패 허용).
    try:
        claude_corrections = await provider.correct_primary(
            claude_pairs, profile, characters, relationships,
            pretreatment.pending_sensitive_hits, knowledge, format_constraint,
        )
    except Exception as exc:
        logger.exception(
            "Claude 1차 교정 실패, 해당 패스를 스킵하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        claude_corrections = []
        warnings.append({"stage": "Claude 1차 교정", "message": str(exc)})
    claude_findings = findings_from_corrections(
        target_version_id, pairs, claude_corrections, stage="claude")
    apply_corrections(pairs, claude_corrections)

    gpt_pairs = [
        {"id": p.id, "korean_text": p.korean.text if p.korean else "",
         "current_text": p.target.text if p.target else ""}
        for p in pairs if p.target is not None
    ]
    try:
        gpt_corrections = await provider.verify_and_refine(
            gpt_pairs, original_target_by_id, profile, knowledge, format_constraint,
        )
    except Exception as exc:
        logger.exception(
            "GPT 2차 검증 실패, 해당 패스를 스킵하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        gpt_corrections = []
        warnings.append({"stage": "GPT 2차 검증", "message": str(exc)})
    gpt_findings = findings_from_corrections(
        target_version_id, pairs, gpt_corrections, stage="gpt")
    apply_corrections(pairs, gpt_corrections)

    # S4 최종 안전망: 모든 교정이 끝난 텍스트를 기준으로 글자수·온점을 마지막
    # 으로 다시 검사한다 — GPT 패스가 문장을 늘리면서 새 위반을 만들 수 있어
    # 앞에서 한 번만 걸러서는 안 된다(design §핵심 설계 포인트: "앞에서 한 번만
    # 걸러선 안 됨"). 온점은 규칙 기반 자동보정이라 여기서도 LLM 없이 바로
    # 재적용한다.
    final_ellipsis_violations = check_ellipsis(pairs)
    final_fixed_by_segment = {v.segment_id: v.fixed_text for v in final_ellipsis_violations}
    for pair in pairs:
        if pair.id in final_fixed_by_segment:
            pair.target.text = final_fixed_by_segment[pair.id]

    line_length_violations = check_line_length(pairs)
    safety_net_findings = await shrink_violating_lines(
        pairs, line_length_violations, provider, target_version_id)
    # line_length_violations는 여기 담아 반환하지 않는다 — shrink_violating_lines가
    # 이미 텍스트를 줄이고 그 결과를 category="formatting" Finding으로 반환했다.
    # 그런데도 여기 다시 담으면 repositories.py가 "이미 줄어든 뒤" 텍스트를
    # original_text로 삼아 별도의 pending formatting finding을 하나 더 만든다 —
    # 검수자에게 사실과 다른(더 이상 위반이 아닌) 원문을 보여주는 중복 레코드다.
    # 온점 위반은 safety_net 같은 별도 Finding 생성 경로가 없는(규칙 기반
    # 자동보정이 전부인) 경우라 여기서 그대로 반환해야 한다.
    format_violations = ellipsis_violations + final_ellipsis_violations

    return {
        "pairs": pairs,
        "format_violations": format_violations,
        "characters": characters,
        "relationships": relationships,
        "segment_resolutions": segment_resolutions,
        "video_path": video_path,
        "video_proxy_path": video_proxy_path,
        "korean_segments_raw": korean_raw,
        "warnings": warnings,
        "findings": (
            pretreatment.findings + claude_findings + gpt_findings + safety_net_findings
        ),
    }
