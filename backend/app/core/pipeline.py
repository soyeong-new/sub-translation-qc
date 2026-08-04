"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from app.providers.base import ModelProvider
from app.core.ingest import load_srt, extract_audio, generate_video_proxy
from app.core.alignment import align
from app.core.format_rules import check_line_length, check_ellipsis, MAX_LINE_CHARS, MAX_LINES
from app.core.safety_net import shrink_violating_lines
from app.language_profiles.loader import load_profile
from app.knowledge.loader import (
    load_knowledge, load_sensitive_terms, load_glossary, load_cta_patterns,
    load_profanity_dictionary,
)
from app.core.character_registry import build_registry
from app.core.consistency_check import find_gender_conflicts, find_register_conflicts
from app.core.pretreatment import run_pretreatment
from app.core.diffing import findings_from_corrections, apply_corrections
from app.schemas import SegmentText

logger = logging.getLogger(__name__)


async def run_pipeline(video_path: str, target_srt_path: str,
                        language: str, variant: str, target_version_id: str,
                        provider: ModelProvider,
                        prior_characters: Optional[list] = None,
                        prior_relationships: Optional[list] = None,
                        cached_korean_segments: Optional[list] = None,
                        cached_video_proxy_path: Optional[str] = None) -> dict:
    """design §전체 파이프라인의 오케스트레이터. S1(사전/규칙) → S2(Claude 1차)
    → S3(GPT 2차) → S4(최종 안전망) 순서로 순차 실행하며, 각 단계의 diff가
    findings가 된다. 오디오/영상 프록시는 STT 직후 한 번만 생성한다.

    원본 영상 삭제는 여기서 하지 않는다 — 이 함수가 반환한 뒤에도 아직 DB에
    아무것도 영속화되지 않은 상태이므로, 여기서 지우면 프로세스가 이 함수와
    호출자(background.py)의 저장 사이 어딘가에서 죽었을 때 원본도 없고 결과도
    없는 상태가 된다. 호출자가 결과를 실제로 커밋한 뒤에 지우도록
    `video_path`를 결과에 그대로 담아 돌려준다."""
    target_segments = load_srt(target_srt_path)

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
                provider.transcribe(wav_path),
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

    # design §에러 처리: 인물/관계 식별(analyze_characters, 실제 LLM 네트워크
    # 호출)이 실패해도 전체 분석을 실패 처리하지 않는다 — Claude/GPT 패스와
    # 동일한 부분 실패 허용 원칙. 빈 레지스트리로 진행하면 성별/존댓말 관련
    # 질문이 생략될 뿐, 나머지 파이프라인(사전필터/Claude/GPT/안전망)은 이미
    # 든 STT 비용을 낭비하지 않고 계속 진행된다.
    try:
        registry = await build_registry(pairs, profile, provider)
    except Exception:
        logger.exception(
            "인물/관계 식별 실패, 빈 레지스트리로 계속 진행 (target_version_id=%s)",
            target_version_id)
        registry = {"characters": [], "relationships": []}
    characters = prior_characters if prior_characters is not None else registry["characters"]
    relationships = prior_relationships if prior_relationships is not None else registry["relationships"]
    gender_questions = find_gender_conflicts(characters)
    register_questions = find_register_conflicts(relationships)

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
    except Exception:
        logger.exception(
            "Claude 1차 교정 실패, 해당 패스를 스킵하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        claude_corrections = []
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
    except Exception:
        logger.exception(
            "GPT 2차 검증 실패, 해당 패스를 스킵하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        gpt_corrections = []
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
        "gender_questions": gender_questions,
        "register_questions": register_questions,
        "video_path": video_path,
        "video_proxy_path": video_proxy_path,
        "korean_segments_raw": korean_raw,
        "findings": (
            pretreatment.findings + claude_findings + gpt_findings + safety_net_findings
        ),
    }
