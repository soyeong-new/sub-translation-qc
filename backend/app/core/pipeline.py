"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

import asyncio
import logging
from pathlib import Path
from statistics import median
from typing import Optional
from app.providers.base import ModelProvider
from app.repositories import normalize_character_name
from app.core.ingest import load_srt, extract_audio, generate_video_proxy, split_audio_into_chunks
from app.core.stt_srt_matching import match_stt_words_to_korean_srt, merge_words_by_korean_cue
from app.core.alignment import align, align_by_korean_cue, detect_global_offset
from app.core.embedding_dp_alignment import align_by_embedding_dp, _clean_text_for_embedding

from app.core.format_rules import (
    check_line_length, check_ellipsis, MAX_LINE_CHARS, MAX_LINES,
)
from app.core.safety_net import shrink_violating_lines, enforce_line_length
from app.language_profiles.loader import load_profile
from app.knowledge.loader import (
    load_knowledge, load_sensitive_terms, load_glossary, load_cta_patterns,
    load_profanity_dictionary,
)
from app.core.pretreatment import run_pretreatment
from app.core.grammar_necessity import (
    check_grammar_necessity, resolve_gender_in_texts, resolve_gender_groups_in_texts,
    _strip_html_tags, _detect_korean_gender,
)
from app.schemas import SegmentText, AlignedPair, Finding

logger = logging.getLogger(__name__)

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
    분할 없이 원본 경로를 그대로 반환했을 수 있으므로(짧은 오디오), 조각
    정리(unlink) 시 원본 wav_path는 그 경로로는 절대 지우지 않는다 — 분할이
    안 일어난 경우 원본 정리는 호출자의 finally 블록이 전담한다.

    반면 분할이 실제로 일어난 경우(chunk_paths가 원본과 다른 실제 조각
    파일들)에는, 조각들이 원본을 바이트 단위로 완전히 대체하는 복사본이라
    원본이 그 순간부터 죽은 데이터가 된다 — 병렬 transcribe가 도는 동안
    원본(~230MB급)까지 같이 들고 있으면 오디오 관련 디스크 사용량이 이
    기능이 지원하려는 바로 그 상황(긴 콘텐츠)에서 불필요하게 두 배가 된다.
    그래서 이 경우엔 transcribe를 시작하기 전에 원본을 바로 지운다."""
    chunk_paths = await asyncio.to_thread(
        split_audio_into_chunks, wav_path, STT_CHUNK_SECONDS)
    if chunk_paths != [wav_path]:
        Path(wav_path).unlink(missing_ok=True)
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
    if not merged:
        # 개별 조각에 세그먼트가 없는 것(무음 구간)은 정상이지만, 모든
        # 조각을 병합한 결과가 통째로 비어 있다면 에피소드 전체에 대사가
        # 없다는 뜻이다 — 이 경우는 여전히 진짜 실패다(과거 조각 분할 이전,
        # transcribe가 에피소드 전체를 한 번에 처리하던 시절과 동일한 판단
        # 기준을 병합 레벨로 옮긴 것).
        raise ValueError("GPT STT 응답에 세그먼트가 없음")
    return merged


async def _run_stt_and_proxy(provider: ModelProvider, video_path: str) -> tuple[list, str]:
    """오디오 추출 + STT + 영상 저화질 프록시 생성을 병렬로 실행한다.
    korean_srt_path 유무와 무관하게 이제 STT는 항상 돌기 때문에, 기존
    "STT 생략" 분기와 "일반 STT" 분기가 이 로직을 공유한다."""
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
    return korean_raw, video_proxy_path


_VIDEO_SYNC_ANCHOR_CUES = 10
_VIDEO_SYNC_CLIP_MARGIN_SECONDS = 90.0


def _median_offset_from_stt_matches(matched_words: list, raw_cues: list,
                                     clip_duration: float) -> Optional[float]:
    """match_stt_words_to_korean_srt가 돌려준 단어별 실측 타이밍 중, 실제로
    STT가 커버한 구간(clip_duration) 안의 큐만 후보로 큐별 "실측 시작 시각 -
    SRT 표기 시작 시각" 차이의 중앙값을 구한다. 개수(앞쪽 몇 개)가 아니라
    시간으로 자르는 이유: 실측으로 확인된 오탐 — 앞쪽 몇 줄이 이름·감탄사처럼
    STT가 놓치기 쉬운 대사에 몰려 있으면, 이미 같은 클립 안에 있는(=추가
    STT 비용 없이 쓸 수 있는) 그 다음 줄들의 멀쩡한 확정 매칭까지 개수
    제한 때문에 버려져 "동기화 지점을 못 찾음"으로 잘못 실패했다(영상
    자체는 실제로 맞는데도). confirmed=False(양옆 확정 지점 사이에서 글자수
    비례로 추정한 값, 실측 아님)인 단어는 아예 근거에서 뺀다 — 고유명사
    (인명·마스코트 이름 등)를 STT가 잘못 들어 확정을 못 한 큐를 실측인
    것처럼 오프셋 계산에 넣었다가 실제로 안 맞는 사례가 확인됐다(design
    §2026-08 영상 동기화 버그 수정). 한 큐 안 첫 "확정" 매칭 단어의
    시각만 쓴다. 중앙값을 쓰는 이유는 그래도 한 큐가 튀면(예: 확정됐지만
    문맥상 이상한 매칭) 평균처럼 전체가 끌려가지 않게 하기 위해서다.
    확정 매칭된 큐가 하나도 없으면 None(사람에게 알려야 할 실패)."""
    offset_by_cue_index: dict = {}
    for w in matched_words:
        idx = w["cue_index"]
        if idx >= len(raw_cues) or idx in offset_by_cue_index or not w.get("confirmed"):
            continue
        if raw_cues[idx].start >= clip_duration:
            continue
        offset_by_cue_index[idx] = w["start"] - raw_cues[idx].start
    if not offset_by_cue_index:
        return None
    return median(offset_by_cue_index.values())


async def _detect_raw_video_sync_offset(
    provider: ModelProvider, video_path: str, korean_srt_path: str,
    raw_cues: list, target_version_id: str, warnings: list,
) -> Optional[float]:
    """한국어 SRT를 직접 올린 경우(korean_srt_path)엔 STT를 아예 안 거치므로,
    실제 영상 파일의 진짜 오디오 타이밍을 시스템 어디도 모른다 — 한국어
    SRT와 대상언어 SRT가 서로 잘 맞아도, 둘 다 업로드한 영상 파일 자체와는
    어긋나 있을 수 있다(예: 자막은 안 잘랐는데 영상만 인트로를 잘라 올림).
    영상 전체를 STT하는 대신 앞쪽 몇 큐 분량만 잘라 STT해서 실측 타이밍을
    확보하고, 그 구간에서 한국어 SRT **원본**(global_offset 보정 전) 표기
    시각과의 차이(중앙값)를 반환한다 — "실측 시각 - 한국어 SRT 원본 시각"
    이지, 최종 video_offset_seconds가 아니다(design §2026-08 영상 동기화
    버그 수정). 호출자가 이 값을 global_offset과 합쳐야 한다 — 프론트가
    seek 기준으로 쓰는 Segment.start는 한국어가 아니라 **대상언어 SRT의
    시계**이고, 대상언어 시계 ≈ 한국어 SRT 원본 시계 + global_offset이기
    때문이다(korean_cues를 global_offset만큼 옮겨서 정렬하는 것과 같은
    이유). 탐지 실패(음성 인식 실패, 매칭 실패 등)는 예외를 던지지 않고
    None을 반환하며 warnings에 남긴다 — 조용히 넘기지 않는다."""
    if not raw_cues:
        return None
    anchor_cues = raw_cues[:_VIDEO_SYNC_ANCHOR_CUES]
    clip_duration = anchor_cues[-1].end + _VIDEO_SYNC_CLIP_MARGIN_SECONDS

    try:
        wav_path = await asyncio.to_thread(
            extract_audio, video_path, duration_seconds=clip_duration)
        try:
            stt_words = await provider.transcribe(wav_path)
        finally:
            Path(wav_path).unlink(missing_ok=True)
    except Exception as exc:
        logger.exception(
            "영상 동기화용 앞부분 STT 실패, 영상 재생 오프셋 확인 생략 "
            "(target_version_id=%s)", target_version_id)
        warnings.append({"stage": "영상 동기화", "message": str(exc)})
        return None

    if not stt_words:
        warnings.append({
            "stage": "영상 동기화",
            "message": "영상 앞부분에서 음성을 인식하지 못해 영상 재생 동기화를 확인하지 못했습니다.",
        })
        return None

    matched_words = match_stt_words_to_korean_srt(stt_words, korean_srt_path)
    offset = _median_offset_from_stt_matches(matched_words, raw_cues, clip_duration)
    if offset is None:
        # 검수 화면에는 경고를 안 띄운다 — 이 탐지가 실패해도 폴백은
        # "영상 고유 오프셋 없음"으로 안전하고(§_run_pipeline_phase1의
        # video_offset_seconds 계산 참고), 이 좁은 보조 확인 하나가 실패한
        # 것만으로 검수자에게 "동기화가 의심된다"고 매번 경고하면 실제로는
        # 멀쩡한 영상에도 반복적으로 뜨는 오탐 노이즈가 된다. 로그에는 남긴다.
        logger.info(
            "영상 동기화 탐지: 확정 매칭 없음, 오프셋 보정 없이 진행 "
            "(target_version_id=%s)", target_version_id)
        return None
    return offset


_GENDER_CONTEXT_RADIUS = 2


def _context_window(pairs: list, center_idx: int, radius: int = _GENDER_CONTEXT_RADIUS) -> tuple[list, list]:
    """center_idx 앞뒤로 radius줄씩 잘라 (before, after)로 반환한다 — 성별
    판단 대상 문장 자체엔 지칭어가 없어도 바로 옆 대사에 그 인물의 이름이나
    성별을 알려주는 표현이 있는 경우가 많아서다(design §문맥 부족으로 인한
    오판). korean/target 둘 다 있는 줄만 포함하고, 배열 경계(에피소드
    시작/끝)에서는 있는 만큼만 반환한다."""
    def _texts(idx: int) -> Optional[dict]:
        p = pairs[idx]
        if p.target is None or p.korean is None:
            return None
        return {"korean_text": p.korean.text, "target_text": p.target.text}

    before = [t for idx in range(max(0, center_idx - radius), center_idx)
              if (t := _texts(idx)) is not None]
    after = [t for idx in range(center_idx + 1, min(len(pairs), center_idx + radius + 1))
             if (t := _texts(idx)) is not None]
    return before, after


async def _run_grammar_necessity_check(
    pairs: list, profile: dict, provider: ModelProvider, target_version_id: str,
    known_gender_facts: Optional[dict] = None, episode_gender_facts: Optional[dict] = None,
) -> tuple[list, list]:
    """문법 필요성 판단(줄 단위, 대상언어는 spaCy·한국어는 kiwipiepy 형태소
    분석): 성별/격식 판단이 실제로 필요한 줄만 골라낸다. 성별 값은 한국어
    원문(어미/호칭, 후보가 1개일 때만)으로 먼저 자동 판정을 시도하고 — 안
    되면 후보가 있는 줄은 전부(근거 단서가 있든 없든) LLM
    (resolve_gender_from_context)이 문장 자체+앞뒤 각 2줄 문맥+한국어
    원문을 보고 그룹핑(is_person/group_id/referent)까지 판단한다 — 이건
    항상 신뢰한다. 다만 gender 필드만은 별도로 검증한다: 한국어 원문에
    성별 근거 단어(강한 단서든 새끼/인간류 약한 단서든)가 하나도 없는
    줄은, LLM이 gender에 뭘 채워 보내든 코드가 무조건 null로 덮어쓴다 —
    근거가 없다는 게 LLM을 부르기 전에 이미 확인된 사실이라, 그런
    줄에서는 LLM이 확신에 찬 오답을 내는 경향이 실측으로 반복 확인됐다
    (design §2026-08 성별판정 정확도 개선). 문장 자체엔 단서가 없어도
    바로 옆 대사에 이름/호칭이 있는 경우가 있어 앞뒤 문맥을 같이 준다.
    반환값은 (segment_resolutions, warnings)."""
    pair_index_by_id = {p.id: i for i, p in enumerate(pairs)}
    korean_text_by_id = {p.id: (p.korean.text if p.korean else "") for p in pairs}
    grammar_pairs = [
        {"id": p.id, "target_text": p.target.text if p.target else "",
         "korean_text": p.korean.text if p.korean else ""}
        for p in pairs if p.target is not None and p.korean is not None
    ]

    segment_resolutions: list = []
    warnings: list = []
    try:
        # spaCy/kiwipiepy 형태소 분석은 둘 다 CPU 바운드 동기 작업이라,
        # asyncio.to_thread로 감싸지 않으면 이 코루틴이 이벤트 루프를 막는다
        # — extract_audio/generate_video_proxy와 동일한 이유.
        grammar_flags = await asyncio.to_thread(
            check_grammar_necessity, grammar_pairs, profile)
        flags_by_id = {f["id"]: f for f in grammar_flags}
        flagged_pairs = [
            p for p in pairs
            if flags_by_id.get(p.id, {}).get("gender_check_needed")
            or flags_by_id.get(p.id, {}).get("formality_check_needed")
        ]

        # 한국어 규칙이 이미 확정한(후보 1개뿐인 줄만 가능) 것은 LLM을
        # 부를 필요가 없다 — 그 외 후보가 있는 줄만 배치로 묶어 한 번에
        # 판단받는다(gloss_gender_words와 같은 이유로 영화 전체를 한
        # 콜에 몰아넣는다 — 항목이 word+context 수준으로 가벼움).
        def _llm_item(p) -> dict:
            context_before, context_after = _context_window(pairs, pair_index_by_id[p.id])
            return {
                "id": p.id, "target_text": p.target.text if p.target else "",
                "korean_text": p.korean.text if p.korean else "",
                "candidate_words": flags_by_id[p.id]["candidate_words"],
                "candidate_word_lemmas": flags_by_id[p.id]["candidate_word_lemmas"],
                "context_before": context_before, "context_after": context_after,
            }

        llm_items = [
            _llm_item(p) for p in flagged_pairs
            if flags_by_id[p.id]["candidate_words"]
            and flags_by_id[p.id]["resolved_gender_from_korean"] is None
        ]
        gender_groups_by_id: dict = {}
        # ponytail: 영화 전체 llm_items를 한 콜로 보낸다 — _split_into_scenes/
        # _verify_chunk(AI 검증)처럼 씬 단위로 청킹하지 않는다. 토큰 한도로
        # 이 콜이 실패하면 영화 전체가 미확정 그룹 폴백으로 넘어간다(문장별
        # 데이터 유실은 없음, 다만 전부 사람에게 확인받게 됨 — 위 except가
        # 이미 그렇게 처리). 항목이 word+context 수준으로 가벼워 아직은
        # 문제된 적이 없다. 실제 영화 길이로 토큰 한도에 걸리기 시작하면
        # _split_into_scenes 같은 씬 단위 청킹으로 승급.
        if llm_items:
            wire_items = [
                {"id": i["id"], "target_text": i["target_text"],
                 "korean_text": i["korean_text"], "candidate_words": i["candidate_words"],
                 "context_before": i["context_before"], "context_after": i["context_after"]}
                for i in llm_items
            ]
            # LLM이 완전히 실패하거나(예외), 응답에서 특정 id가 통째로
            # 빠지면(스키마는 지켰지만 그 id를 안 돌려준 부분 실패) 쓸
            # 폴백 — "과탐지 허용, 누락 금지" 방침대로 후보 단어 전체를
            # 미확정 그룹 하나로 만들어 사람에게 넘긴다.
            fallback_groups_by_id = {
                i["id"]: [{
                    "group_index": 0, "referent": None, "character_name": None,
                    "words": i["candidate_words"], "target_word_lemmas": i["candidate_word_lemmas"],
                    "candidate_indices": list(range(len(i["candidate_words"]))),
                    "gender": None, "suggested_gender": None, "human_confirmed": False,
                }]
                for i in llm_items
            }
            try:
                llm_results = await provider.resolve_gender_from_context(wire_items, profile)
                llm_groups_by_id = _build_gender_groups_from_llm(llm_items, llm_results)
                # _build_gender_groups_from_llm은 응답에 실제로 포함된 id만
                # 키로 넣는다(빈 리스트 포함 가능 — "전부 사람 아님"이라는
                # 유효한 판단) — 응답에서 아예 빠진 id만 폴백으로 채운다.
                for item in llm_items:
                    llm_groups_by_id.setdefault(item["id"], fallback_groups_by_id[item["id"]])
                # is_person/group_id/referent는 LLM 응답을 그대로 신뢰한다 —
                # 이번 세션 내내 문제였던 건 gender 필드뿐이었다. has_gender_hint가
                # False인 줄(한국어 원문에 성별 근거 단어가 하나도 없음)은
                # LLM이 뭐라고 답했든 gender만 null로 덮어쓴다 — 근거가 없다는
                # 건 LLM을 부르기 전에 이미 확인된 사실이라, LLM의 자기 확신도
                # 보고를 믿을 근거가 안 된다(design §2026-08 성별판정 정확도
                # 개선 — 근거 없는 문장에서 LLM이 확신에 찬 오답을 내는 경향이
                # 실측으로 반복 확인됨).
                for item in llm_items:
                    if flags_by_id[item["id"]]["has_gender_hint"]:
                        continue
                    for group in llm_groups_by_id.get(item["id"], []):
                        group["gender"] = None
                # 위에서 gender를 지웠어도(또는 LLM이 애초에 null로 남겼어도),
                # referent 서술 자체에 이미 성별을 알 수 있는 한국어 지칭어가
                # 담긴 사례가 실측으로 확인됐다("아빠", "특정 여성 인물" 등) —
                # LLM이 referent와 gender 필드를 항상 일관되게 연결하지는
                # 않는다는 뜻이다. LLM의 "gender" 주장을 다시 믿는 게 아니라,
                # 이미 신뢰하는 한국어 호칭 사전(_detect_korean_gender, 대사
                # 문장 판정과 동일한 기준)으로 referent를 독립적으로 재검증
                # 하는 것이라 위 안전장치와 상충하지 않는다.
                for groups in llm_groups_by_id.values():
                    for group in groups:
                        if group["gender"] is None and group["referent"]:
                            group["gender"] = _detect_korean_gender(group["referent"])
                gender_groups_by_id.update(llm_groups_by_id)

                # 이 title에서 이미 확인된 캐릭터 이름이면 gender를 바로
                # 채운다(design §시리즈/다국어 간 캐릭터 성별 재사용 —
                # 자동 적용으로 전환, 2026-08-25 사용자 결정: 동명이인 오적용
                # 리스크보다 매 회차/언어마다 같은 확인을 반복하는 번거로움이
                # 더 크다고 판단). human_confirmed는 True로 만들지 않는다 —
                # 이번에 사람이 실제로 본 게 아니므로, 이 인스턴스를 다시
                # "재확인된 사실"로 harvest해 재전파하지는 않는다(기존 사실을
                # 그대로 재사용만 함).
                if known_gender_facts or episode_gender_facts:
                    for pair_id, groups in gender_groups_by_id.items():
                        for group in groups:
                            name = group.get("character_name")
                            fact_gender = (
                                known_gender_facts.get(normalize_character_name(name))
                                if known_gender_facts and name else None
                            )
                            group["suggested_gender"] = fact_gender
                            if fact_gender:
                                group["gender"] = fact_gender
                        # 이름 매칭이 안 됐고 인물이 1명뿐인 줄이면, 같은 회차의
                        # 다른 언어 버전에서 정확히 같은 위치·같은 한국어
                        # 원문으로 이미 확인된 값이 있는지 추가로 시도한다
                        # (design §회차 내 문장 기준 재사용 — 이름 없는 인물도
                        # 커버. 인물 2명 이상인 줄은 언어마다 그룹 순서/개수가
                        # 달라질 수 있어 대응이 안전하지 않으므로 제외).
                        if episode_gender_facts and len(groups) == 1 and not groups[0].get("gender"):
                            key = (pair_index_by_id[pair_id], korean_text_by_id.get(pair_id, ""))
                            episode_fact_gender = episode_gender_facts.get(key)
                            if episode_fact_gender:
                                groups[0]["suggested_gender"] = episode_fact_gender
                                groups[0]["gender"] = episode_fact_gender
                else:
                    for groups in gender_groups_by_id.values():
                        for group in groups:
                            group["suggested_gender"] = None
            except Exception as exc:
                logger.exception(
                    "성별 문맥 판단(LLM) 실패, 해당 줄은 미확정 그룹으로 사람에게 넘김 "
                    "(target_version_id=%s)", target_version_id)
                warnings.append({"stage": "성별 문맥 판단", "message": str(exc)})
                gender_groups_by_id.update(fallback_groups_by_id)

        for p in flagged_pairs:
            flags = flags_by_id[p.id]
            gender_needed = bool(flags.get("gender_check_needed"))
            resolved_gender = flags.get("resolved_gender_from_korean")
            gender_groups = gender_groups_by_id.get(p.id)

            if gender_groups:
                segment_resolutions.append({
                    "segment_id": p.id,
                    "gender_check_needed": True,
                    "formality_check_needed": bool(flags.get("formality_check_needed")),
                    "resolved_gender": None,
                    "resolved_gender_groups": gender_groups,
                    "resolved_formality": flags.get("resolved_formality"),
                })
                continue

            # p.id가 gender_groups_by_id에 키로 있는데(빈 리스트로) 그룹이
            # 없다는 건, LLM이 이 줄의 후보를 전부 "사람 얘기 아님"으로
            # 판단했다는 뜻이다 — 이 줄은 성별 확인이 필요 없다(design
            # §오탐 제거). 애초에 LLM을 부르지 않은 줄(후보 0개, 또는
            # 한국어 규칙으로 이미 끝난 줄)은 이 키 자체가 없으므로 영향
            # 없다.
            if resolved_gender is None and p.id in gender_groups_by_id:
                gender_needed = False

            segment_resolutions.append({
                "segment_id": p.id,
                "gender_check_needed": gender_needed,
                "formality_check_needed": bool(flags.get("formality_check_needed")),
                "resolved_gender": resolved_gender,
                "resolved_gender_groups": None,
                "resolved_formality": flags.get("resolved_formality"),
            })
    except Exception as exc:
        logger.exception(
            "문법 필요성 판단 실패, 성별/격식 체크 생략하고 계속 진행 (target_version_id=%s)",
            target_version_id)
        warnings.append({"stage": "문법 필요성 판단", "message": str(exc)})
    return segment_resolutions, warnings


def _build_gender_groups_from_llm(llm_items: list, llm_results: list) -> dict:
    """resolve_gender_from_context의 후보 단어별 판단(words: [{"index",
    "is_person","group_id","gender","referent"}, ...])을, 검수자에게
    보여주고 DB에 저장할 인물별 그룹 형태로 묶는다. is_person이 false인
    후보는 그룹을 만들지 않는다(사람 얘기가 아니므로 확인 대상에서 제외).
    candidate_indices는 나중에 resolve_gender_groups_in_texts가 같은
    텍스트를 다시 파싱했을 때 같은 순서로 후보를 찾아 정확히 그 단어에만
    성별을 적용하는 데 쓰인다(spaCy 의존구문 재분석 없이 등장 순서로만
    매칭 — design §그룹핑도 LLM이 직접). 반환값은 {id: [group, ...]} —
    id에 대응하는 값이 없으면(LLM 응답에 그 id가 통째로 빠졌으면) 그 id는
    아예 키에 안 들어간다(호출자가 이걸 "응답 누락"으로 보고 폴백을
    채운다). LLM이 그 id는 포함했지만 모든 후보가 is_person=false였으면
    "그룹 없음"이라는 유효한 판단이므로 빈 리스트로(키는 존재) 넣는다 —
    이 둘을 구분해야 호출자가 "사람 얘기 아님이라 질문 불필요"와 "응답
    자체가 없어 안전하게 다시 물어봐야 함"을 다르게 처리할 수 있다."""
    items_by_id = {i["id"]: i for i in llm_items}
    results_by_id = {r["id"]: r for r in llm_results}
    groups_by_id: dict = {}
    for item_id, item in items_by_id.items():
        candidate_words = item["candidate_words"]
        candidate_lemmas = item["candidate_word_lemmas"]
        result = results_by_id.get(item_id)
        words_info = result.get("words") if result else None
        if words_info is None:
            continue
        by_group: dict = {}
        order: list = []
        for w in words_info:
            idx = w.get("index")
            if not w.get("is_person") or idx is None or idx < 0 or idx >= len(candidate_words):
                continue
            group_id = w.get("group_id")
            if group_id not in by_group:
                by_group[group_id] = {
                    "referent": w.get("referent"), "character_name": w.get("character_name"),
                    "words": [], "target_word_lemmas": [],
                    "candidate_indices": [], "gender": w.get("gender"),
                    "human_confirmed": False,
                }
                order.append(group_id)
            entry = by_group[group_id]
            entry["words"].append(candidate_words[idx])
            entry["target_word_lemmas"].append(
                candidate_lemmas[idx] if idx < len(candidate_lemmas) else candidate_words[idx].lower())
            entry["candidate_indices"].append(idx)
            # 같은 그룹 안의 후보끼리 성별 판단이 갈리면(정상적인 LLM 출력
            # 이라면 안 생겨야 하지만, 방어적으로) 그룹 전체를 미확정으로
            # 남긴다 — 절반만 적용하면 더 위험하다. 단, 한쪽이 null(그
            # 후보 단어 자체는 성별 단서가 없어 확정 못한 것뿐)인 건
            # 충돌이 아니다 — 그룹 안 다른 후보가 이미 확정한 성별을
            # 그대로 채워 쓴다. 예전엔 이 구분이 없어서, 한 그룹 안에
            # 성별이 확실한 후보와 애매한 후보가 섞이면(흔한 정상 케이스,
            # 예: 형용사 하나는 명확히 성별 표시, 다른 하나는 무관한 어미)
            # 이미 확정된 성별까지 매번 null로 지워졌다 — 인물이 한 명뿐인
            # 줄에서도 성별이 어이없이 사라지던 원인 중 하나로 보인다.
            new_gender = w.get("gender")
            if new_gender is not None:
                if entry["gender"] is None:
                    entry["gender"] = new_gender
                elif new_gender != entry["gender"]:
                    entry["gender"] = None
            # character_name도 마찬가지로 그룹 안에서 갈리면(예: 서로 다른
            # 인물이 같은 group_id로 잘못 합쳐진 경우, 실측: 보나+찬영이
            # "차은상"으로 뭉쳐짐) 그룹 전체를 미확정으로 남긴다 — 틀린
            # 이름표를 달고 사람에게 확인받으면 그 오답이 character_gender_facts에
            # title 전체로 영구 저장돼 계속 퍼진다(design §그룹 내 인물
            # 일관성 검증). 단, 어느 한쪽이 null(그 후보에서는 이름 단서가
            # 없었을 뿐)인 건 충돌이 아니다 — 둘 다 값이 있는데 서로 다를
            # 때만 진짜 충돌이다. null이던 쪽은 값이 있는 쪽으로 채운다.
            new_name = w.get("character_name")
            if new_name is not None:
                if entry["character_name"] is None:
                    entry["character_name"] = new_name
                elif new_name != entry["character_name"]:
                    entry["character_name"] = None
                    entry["gender"] = None
        groups_by_id[item_id] = [
            {"group_index": i, **by_group[gid]} for i, gid in enumerate(order)
        ]
    return groups_by_id


async def _gloss_gender_words(
    segment_resolutions: list, pairs: list, provider: ModelProvider, profile: dict,
    target_version_id: str, warnings: list,
) -> None:
    """성별 확인이 걸린 단어들의 뜻을 LLM 한 번(배치)으로 한국어로 풀이해
    segment_resolutions를 제자리에서(in-place) 채운다 — 대상언어를 모르는
    검수자가 "이 단어가 사람 얘기인지 사물 얘기인지"조차 판단 못 하는 문제를
    돕는다. 실패해도 파이프라인을 막지 않는다 — 뜻풀이 없이(단어만 보여주는
    상태로) 계속 진행한다."""
    pair_by_id = {p.id: p for p in pairs}
    entries: list = []
    items: list = []
    for r in segment_resolutions:
        pair = pair_by_id.get(r["segment_id"])
        if pair is None or pair.target is None:
            continue
        groups = r.get("resolved_gender_groups")
        if not groups:
            continue
        for group_index, group in enumerate(groups):
            for w in group["words"]:
                items.append({"id": str(len(entries)), "word": w, "context": pair.target.text})
                entries.append((r["segment_id"], w, group_index))
    if not items:
        return
    try:
        results = await provider.gloss_words(items, profile)
    except Exception as exc:
        logger.exception(
            "성별 표시 단어 뜻풀이 실패, 뜻풀이 없이 계속 진행 (target_version_id=%s)",
            target_version_id)
        warnings.append({"stage": "단어 뜻풀이", "message": str(exc)})
        return
    meaning_by_idx = {r["id"]: r.get("meaning") for r in results}
    group_meanings_by_segment: dict = {}
    for idx, (segment_id, word, group_index) in enumerate(entries):
        meaning = meaning_by_idx.get(str(idx))
        if not meaning:
            continue
        group_meanings_by_segment.setdefault(segment_id, {}).setdefault(group_index, {})[word] = meaning
    for r in segment_resolutions:
        groups = r.get("resolved_gender_groups")
        if not groups:
            continue
        group_meanings = group_meanings_by_segment.get(r["segment_id"]) or {}
        for group_index, group in enumerate(groups):
            meanings = group_meanings.get(group_index)
            if meanings:
                group["word_meanings"] = meanings


def _dedupe_by_segment_id(corrections: list) -> dict:
    """동일 segment_id에 대해 correction이 두 개 이상 오면 첫 번째만 채택한다
    (LLM이 프롬프트 계약을 어기고 같은 세그먼트를 중복 반환할 가능성에 대비)."""
    seen: dict = {}
    for c in corrections:
        seen.setdefault(c["segment_id"], c)
    return seen


def _reconcile_dual_verification(
    claude_corrections: list, gpt_corrections: list,
) -> tuple[list, list, list]:
    """Claude/GPT가 각각 독립적으로 낸 교정 제안을 segment_id 기준으로 비교한다.
    같은 줄을 둘 다 지적한 경우는 아직 "합의 후보"일 뿐이다 — 문구가 다를 수
    있어(두 LLM이 토씨까지 똑같이 쓸 확률은 낮음), segment_id만으로 합의를
    확정하면 안 된다. 실제 합의 여부는 _check_equivalence가 두 문구의 의미가
    같은지 교차 확인한 뒤에 정해진다. 반환값은
    (candidate_pairs, claude_only, gpt_only) — candidate_pairs는
    (claude_correction, gpt_correction) 튜플 리스트."""
    claude_by_id = _dedupe_by_segment_id(claude_corrections)
    gpt_by_id = _dedupe_by_segment_id(gpt_corrections)
    candidate_ids = claude_by_id.keys() & gpt_by_id.keys()
    claude_only_ids = claude_by_id.keys() - gpt_by_id.keys()
    gpt_only_ids = gpt_by_id.keys() - claude_by_id.keys()

    candidate_pairs = [(claude_by_id[sid], gpt_by_id[sid]) for sid in candidate_ids]
    claude_only = [claude_by_id[sid] for sid in claude_only_ids]
    gpt_only = [gpt_by_id[sid] for sid in gpt_only_ids]
    return candidate_pairs, claude_only, gpt_only


async def _safe_call(coro, label: str, note: str, target_version_id: str, warnings: list) -> list:
    """실패해도 파이프라인 전체를 죽이지 않고 빈 결과로 대체한다 — 호출부가
    warnings 리스트에 사유를 남겨 검수자에게 알린다."""
    try:
        return await coro
    except Exception as exc:
        logger.exception(
            "%s 실패, %s (target_version_id=%s)", label, note, target_version_id)
        warnings.append({"stage": label, "message": str(exc)})
        return []


def _drop_malformed_corrections(corrections: list, label: str, target_version_id: str, warnings: list) -> list:
    """항목 하나하나에 segment_id가 빠진 채로 응답이 오는 경우(모델이 대량 입력에서
    가끔 필드를 누락함 — split_scenes 독스트링 참고) 이후 c["segment_id"] 접근에서
    KeyError가 나 파이프라인 전체가 죽는 걸 막는다. _safe_call은 호출 자체가
    통째로 실패했을 때만 잡아주고, 이렇게 부분적으로 망가진 개별 항목은 못 걸러서
    별도로 걸러야 한다."""
    valid = [c for c in corrections if isinstance(c, dict) and c.get("segment_id")]
    dropped = len(corrections) - len(valid)
    if dropped:
        logger.warning(
            "%s: segment_id 누락 항목 %d건 버림 (target_version_id=%s)",
            label, dropped, target_version_id)
        warnings.append({
            "stage": label,
            "message": f"AI 응답 중 {dropped}건이 형식이 맞지 않아 건너뛰었습니다.",
        })
    return valid


async def _check_equivalence(
    candidate_pairs: list, korean_text_by_id: dict,
    provider: ModelProvider, profile: dict, target_version_id: str,
) -> tuple[list, list, list, list]:
    """합의 후보(같은 줄을 둘 다 지적했지만 문구는 다를 수 있음)가 실제로 같은
    뜻인지 Claude/GPT 양쪽에 교차로 물어 확인한다 — 병합 판정을 한쪽 모델에만
    맡기면 "합의"라는 신뢰 신호 자체가 다시 단일 모델 신뢰 문제로 돌아가므로,
    둘 다 "같다"고 해야만 진짜 합의로 확정한다(호출 실패도 안전하게 불일치로
    처리). 반환값은 (true_agreed, disputed_claude, disputed_gpt, warnings)."""
    warnings: list = []
    if not candidate_pairs:
        return [], [], [], warnings

    items = [
        {"id": c["segment_id"], "korean_text": korean_text_by_id.get(c["segment_id"], ""),
         "text_a": c["corrected_text"], "text_b": g["corrected_text"]}
        for c, g in candidate_pairs
    ]

    claude_verdicts, gpt_verdicts = await asyncio.gather(
        _safe_call(provider.check_equivalence_with_claude(items, profile),
                   "합의 동등성 확인(Claude)", "해당 후보들은 불일치로 안전하게 처리",
                   target_version_id, warnings),
        _safe_call(provider.check_equivalence_with_gpt(items, profile),
                   "합의 동등성 확인(GPT)", "해당 후보들은 불일치로 안전하게 처리",
                   target_version_id, warnings),
    )
    claude_verdict_by_id = {v["id"]: v["equivalent"] for v in claude_verdicts}
    gpt_verdict_by_id = {v["id"]: v["equivalent"] for v in gpt_verdicts}

    true_agreed: list = []
    disputed_claude: list = []
    disputed_gpt: list = []
    for claude_correction, gpt_correction in candidate_pairs:
        sid = claude_correction["segment_id"]
        both_confirm = (
            claude_verdict_by_id.get(sid, False) and gpt_verdict_by_id.get(sid, False)
        )
        if both_confirm:
            true_agreed.append(gpt_correction)
        else:
            disputed_claude.append(claude_correction)
            disputed_gpt.append(gpt_correction)
    return true_agreed, disputed_claude, disputed_gpt, warnings


def _chunk_list(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


BACK_TRANSLATE_CHUNK_SIZE = 20


async def _back_translate_all(
    texts: list, profile: dict, call_fn, label: str, target_version_id: str, warnings: list,
) -> list:
    """texts를 BACK_TRANSLATE_CHUNK_SIZE 단위로 쪼개 병렬로 역번역한다. 영화 전체를
    한 콜에 몰아넣으면 응답이 토큰 한도에서 잘려 JSON 파싱이 통째로
    실패하거나(그 콜 전체의 역번역이 사라짐), 항목이 많을수록 모델이
    id를 엉뚱한 항목에 붙이는 오귀속도 늘어난다 — _verify_chunk가 이미
    같은 이유로 씬 단위 청킹을 쓰고 있다(CHUNK_MAX_SIZE 주석 참고). 역번역은
    항목마다 독립적인 번역이라 씬 경계를 지킬 필요가 없어 단순 고정 크기
    청킹으로 충분하다. 청크 하나가 실패해도(_safe_call) 그 청크만 역번역
    없이 넘어가고 나머지는 살아남는다 — 전체를 한 콜로 보낼 때보다 실패
    영향 범위가 훨씬 작다. 실패 자체는 한 번 재시도한다 — 실측(프로덕션):
    LLM이 JSON 포맷을 한 번 깨는 건 대개 일시적 노이즈라, 완전히
    포기하기 전에 한 번 더 시도하면 대부분 복구된다."""
    if not texts:
        return []

    async def _call_with_one_retry(chunk):
        try:
            return await call_fn(chunk, profile)
        except Exception:
            return await call_fn(chunk, profile)

    chunk_results = await asyncio.gather(*[
        _safe_call(_call_with_one_retry(chunk), label,
                   "해당 구간은 역번역 없이 계속 진행", target_version_id, warnings)
        for chunk in _chunk_list(texts, BACK_TRANSLATE_CHUNK_SIZE)
    ])
    return [r for chunk in chunk_results for r in chunk]


async def _back_translate_proposals(
    provider: ModelProvider, profile: dict,
    agreed: list, claude_only: list, gpt_only: list, target_version_id: str,
    pairs: list,
) -> tuple[dict, dict, set, list]:
    """제안된 문구를 반대쪽 모델이 한국어로 역번역하고(감사/참고용), 동시에
    그 제안이 교정 전 원문보다 실제로 나아졌는지도 같은 호출에서 판단시킨다
    — 자기가 쓴 문구를 자기가 평가하면 스스로의 오류를 매끄럽게 얼버무려
    가릴 위험이 있어(같은 모델의 왕복 번역/판단은 오류를 숨기는 경향) 항상
    교차 검증한다. claude_only는 GPT가, (agreed + gpt_only는 전부 GPT
    문구이므로) Claude가 판단한다. 원문(original_text)도 같이 역번역시켜
    리뷰어가 "교정 전엔 뭐였는지"를 제안문 역번역과 나란히 비교할 수 있게
    한다(design §리뷰어가 스페인어를 몰라 원문의 뉘앙스가 더 나은 경우를
    놓치는 문제). 반환값은 (segment_id -> 제안문 한국어 역번역,
    segment_id -> 원문 한국어 역번역, 개선 아님으로 판정된
    (segment_id, source) 집합, warnings)."""
    warnings: list = []
    original_text_by_id = {p.id: p.target.text for p in pairs if p.target}
    korean_text_by_id = {p.id: (p.korean.text if p.korean else "") for p in pairs}

    def _to_payload(corrections: list) -> list:
        # <i>...</i> 같은 오프스크린/독백 표시 태그를 지우고 보낸다 — 안
        # 지우면 역번역 LLM한테 불필요하게 텍스트 길이만 늘리고(청크당
        # 응답 토큰 한도를 더 쉽게 넘기게 만듦), 있으나 마나 한 마크업을
        # 대사처럼 오인시킬 위험도 있다(design §2026-08 정렬 오류 수정과
        # 같은 종류의 문제).
        return [
            {"id": c["segment_id"],
             "reference_korean": korean_text_by_id.get(c["segment_id"], ""),
             "original_text": _strip_html_tags(original_text_by_id.get(c["segment_id"], "")),
             "text": _strip_html_tags(c["corrected_text"])}
            for c in corrections
        ]

    claude_authored_texts = _to_payload(claude_only)
    gpt_authored_texts = _to_payload(agreed + gpt_only)

    claude_authored_backtranslated, gpt_authored_backtranslated = await asyncio.gather(
        _back_translate_all(
            claude_authored_texts, profile, provider.back_translate_with_gpt,
            "Claude 제안 역번역", target_version_id, warnings),
        _back_translate_all(
            gpt_authored_texts, profile, provider.back_translate_with_claude,
            "GPT 제안 역번역", target_version_id, warnings),
    )
    # 키를 segment_id만으로 두면, 의견이 갈린(disputed) 세그먼트는 Claude
    # 문구 역번역과 GPT 문구 역번역이 같은 id를 두고 충돌해 하나가 사라진다
    # — "claude_authored"/"gpt_authored"로 원저작자를 구분해 각자의
    # 역번역이 서로 덮어쓰지 않게 한다.
    backtranslation_by_id = {
        **{(r["id"], "claude_authored"): r["korean_text"] for r in claude_authored_backtranslated},
        **{(r["id"], "gpt_authored"): r["korean_text"] for r in gpt_authored_backtranslated},
    }
    original_backtranslation_by_id = {
        (r["id"], "claude_authored"): r["original_korean_text"]
        for r in claude_authored_backtranslated if r.get("original_korean_text")
    } | {
        (r["id"], "gpt_authored"): r["original_korean_text"]
        for r in gpt_authored_backtranslated if r.get("original_korean_text")
    }
    # 판정 호출이 실패하면(_safe_call이 빈 리스트로 대체) is_improvement 키가
    # 아예 없다 — 이때는 "폐기"보다 "일단 보존"이 안전하므로 기본값 True로
    # 둔다(모르면 걸러내지 않는다).
    not_improved = {
        (r["id"], "claude_authored") for r in claude_authored_backtranslated
        if not r.get("is_improvement", True)
    } | {
        (r["id"], "gpt_authored") for r in gpt_authored_backtranslated
        if not r.get("is_improvement", True)
    }
    return backtranslation_by_id, original_backtranslation_by_id, not_improved, warnings


async def _make_dual_verification_finding(
    target_version_id: str, pair, correction: dict,
    status: str, model_label: str, source: str, backtranslation_by_id: dict,
    original_backtranslation_by_id: dict, provider: ModelProvider,
) -> Finding:
    original_text = pair.target.text
    # status="pending"(모델 하나만 지적)인 항목은 검수자가 승인하기 전까지
    # S4 안전망(_run_final_safety_net)을 안 거친다 — 여기서 미리 강제하지
    # 않으면 검수자가 50자 넘는 제안을 승인 전 화면에서 그대로 보게 된다
    # (사용자 재현). status="approved"도 여기서 같이 걸러두면 S4가 같은
    # 세그먼트를 또 검사할 필요가 없다(enforce_line_length는 위반이 없으면
    # LLM을 안 부르므로 여기서 미리 해도 비용이 늘지 않는다).
    corrected_text, _ = await enforce_line_length(correction["corrected_text"], provider)
    correction["corrected_text"] = corrected_text
    description = correction["description"]
    backtranslation = backtranslation_by_id.get((correction["segment_id"], source))
    if backtranslation:
        description = f"{description} (한국어 역번역 참고: {backtranslation})"
    # 리뷰어가 스페인어를 몰라 "원문이 더 나을 수도 있다"를 놓치는 문제
    # (design §Shin Ramyun/Ojalá 오판 사례) — 제안문 역번역 옆에 원문
    # 역번역도 붙여 나란히 비교할 수 있게 한다. ReviewView.jsx의
    # splitDescription이 이 태그를 이미 파싱하므로(STT 재검증 경로와 공유)
    # 프론트엔드 변경은 필요 없다 — 반드시 "한국어 역번역 참고" 태그
    # 뒤(오른쪽)에 붙여야 한다(파싱이 뒤에서부터 벗겨내므로 순서가 바뀌면
    # 정규식이 실패한다).
    original_backtranslation = original_backtranslation_by_id.get(
        (correction["segment_id"], source))
    if original_backtranslation:
        description = f"{description} (원본 한국어 역번역 참고: {original_backtranslation})"
    return Finding(
        id=f"finding_{correction['segment_id']}_{model_label}_{correction['category']}",
        target_version_id=target_version_id, segment_id=correction["segment_id"],
        category=correction["category"], description=description,
        original_text=original_text, suggested_text=corrected_text,
        confidence=1.0, source="llm", model=model_label,
        status=status, final_text=corrected_text if status == "approved" else "",
    )


# 영화 전체 pair를 한 콜에 몰아넣으면 Claude/GPT 응답이 토큰 한도에서 잘려
# JSON 파싱이 통째로 실패하고(그 콜에 담긴 구간 전체가 무효 처리됨), 항목이
# 많을수록 모델이 segment_id를 엉뚱한 줄에 붙이는 오귀속도 늘어난다. 1차
# 방어선은 _split_into_scenes의 LLM 씬 분할(문맥 경계에서 자름)이고, 이
# 함수는 그게 실패했을 때의 전체 폴백이자 씬이 너무 클 때의 2차 안전장치다
# — 대사 사이 타임코드 공백(장면 전환 등 자연스러운 끊김)에서만 자르고,
# 공백 없이 대화가 계속 이어지면 최대 크기에서 강제로 자른다.
CHUNK_MIN_SIZE = 20
CHUNK_MAX_SIZE = 35
CHUNK_GAP_SECONDS = 1.5


def _chunk_pairs_by_gap(pairs: list) -> list[list]:
    chunks: list[list] = []
    current: list = []
    for i, p in enumerate(pairs):
        current.append(p)
        at_max = len(current) >= CHUNK_MAX_SIZE
        gap_ok = False
        if not at_max and len(current) >= CHUNK_MIN_SIZE and i + 1 < len(pairs):
            gap_ok = pairs[i + 1].target.start - p.target.end >= CHUNK_GAP_SECONDS
        if at_max or gap_ok:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _validate_scene_boundaries(scenes: list, filtered_pairs: list) -> Optional[list[list]]:
    """scenes(start_id/end_id 목록)가 filtered_pairs를 처음부터 끝까지
    순서대로 빠짐없이 겹치지 않게 커버하는지 확인한다. 하나라도 어긋나면
    (없는 id, 순서 뒤바뀜, 빈틈, 중복 등) None을 반환해 호출자가 전체를
    타임코드 공백 청킹으로 폴백하게 한다 — 일부만 살리는 부분 폴백은
    경계 접합부 처리가 그 자체로 새 버그 소스가 돼 하지 않는다."""
    id_to_index = {p.id: i for i, p in enumerate(filtered_pairs)}
    chunks: list[list] = []
    expected_start = 0
    for scene in scenes:
        start_idx = id_to_index.get(scene.get("start_id"))
        end_idx = id_to_index.get(scene.get("end_id"))
        if start_idx is None or end_idx is None:
            return None
        if start_idx != expected_start or end_idx < start_idx:
            return None
        chunks.append(filtered_pairs[start_idx:end_idx + 1])
        expected_start = end_idx + 1
    if expected_start != len(filtered_pairs):
        return None
    return chunks


async def _split_into_scenes(
    provider: ModelProvider, profile: dict, filtered_pairs: list,
    target_version_id: str, warnings: list,
) -> list[list]:
    """씬 분할 LLM 콜(GPT, json_schema 강제)로 pairs를 화제/화자/시공간/톤
    전환 기준의 문맥 단위로 나눈다. 응답이 filtered_pairs를 빠짐없이·
    순서대로·안 겹치게 커버하지 못하면(파싱 실패 포함) 1회 재시도하고,
    그래도 실패하면 타임코드 공백 청킹으로 전체 폴백한다. 성공한 씬도
    CHUNK_MAX_SIZE를 넘으면 그 안에서 다시 타임코드 공백으로 쪼갠다 —
    토큰 한도 안전장치는 씬 분할 성공 여부와 무관하게 항상 적용된다."""
    if not filtered_pairs:
        return []
    scene_items = [
        {"id": p.id, "korean_text": p.korean.text if p.korean else "",
         "target_text": p.target.text, "start": p.target.start, "end": p.target.end}
        for p in filtered_pairs
    ]
    for _attempt in range(2):
        try:
            scenes = await provider.split_scenes(scene_items, profile)
            chunks = _validate_scene_boundaries(scenes, filtered_pairs)
        except Exception:
            chunks = None
        if chunks is not None:
            final_chunks = [sub for chunk in chunks for sub in _chunk_pairs_by_gap(chunk)]
            # 성공 시에도 씬 경계를 남긴다 — 실패 때만 기록하면 "이 대사가
            # 실제로 어느 씬으로 묶였는지"를 사후에 확인할 방법이 없어, 씬이
            # 예상과 다르게 쪼개져 문맥이 끊긴 경우(예: 반복되는 시구절이
            # 서로 다른 씬으로 갈라짐)를 디버깅할 수가 없었다. warnings(DB
            # 저장, 리뷰어 화면에 노출됨)엔 안 넣는다 — 정상 실행에 매번
            # "경고"가 뜨는 건 노이즈이므로 서버 로그로만 남긴다.
            # ponytail: logging.info는 앱 전체에 로깅 설정이 없어 기본
            # WARNING 레벨에 걸러져 조용히 사라진다 — 전역 logging 설정을
            # 새로 추가하는 대신 warning 레벨을 그대로 씀(실제 경고는 아니지만
            # 로그에 보이는 게 목적).
            logger.warning(
                "씬 %d개로 분할 (target_version_id=%s): %s", len(final_chunks),
                target_version_id,
                ", ".join(f"{c[0].id}~{c[-1].id}({len(c)}줄)" for c in final_chunks))
            return final_chunks
    warnings.append({
        "stage": "씬 분할",
        "message": "씬 분할 실패, 타임코드 공백 기준 청킹으로 대체",
    })
    return _chunk_pairs_by_gap(filtered_pairs)


async def _reapply_resolved_gender_to_corrections(
    entries: list, profile: dict, resolved_registers: dict,
) -> None:
    """S2가 오역 등 다른 문제를 고치며 문장을 통째로 다시 쓰면, 이미 확정된
    성별이 결과물에도 그대로 남아있다는 보장이 없다 — 프롬프트로 "건드리지
    말라"고 지시만 하는 건 강제력이 없다(design §AI에게 반영해달라 부탁하지
    말고 파이썬이 직접). _apply_resolved_gender가 S2 "이전" 입력에 적용하는
    것과 같은 이유로, S2 "이후" 출력에도 다시 적용해야 한다 — 안 그러면
    LLM이 새로 쓴 문장이 finding.suggested_text/pair.target.text로 그대로
    새어나간다. entries는 (correction, ...) 튜플 리스트 — 같은 segment_id가
    의견 갈림(disputed)으로 두 번(Claude 문구/GPT 문구) 등장할 수 있어
    segment_id 대신 리스트 인덱스를 리졸버 배치 콜의 키로 써서 서로
    덮어쓰지 않게 한다."""
    single_items = []
    group_items = []
    for idx, entry in enumerate(entries):
        correction = entry[0]
        register = resolved_registers.get(correction["segment_id"])
        if not register:
            continue
        if register.get("gender_groups"):
            group_items.append(
                {"id": idx, "text": correction["corrected_text"], "groups": register["gender_groups"]})
        elif register.get("gender"):
            single_items.append(
                {"id": idx, "text": correction["corrected_text"], "gender": register["gender"]})
    if not single_items and not group_items:
        return
    fixed_by_idx: dict = {}
    if single_items:
        fixed_by_idx.update(await asyncio.to_thread(
            resolve_gender_in_texts, single_items, profile.get("language")))
    if group_items:
        fixed_by_idx.update(await asyncio.to_thread(
            resolve_gender_groups_in_texts, group_items, profile.get("language")))
    for idx, entry in enumerate(entries):
        if idx in fixed_by_idx:
            entry[0]["corrected_text"] = fixed_by_idx[idx]


async def _run_dual_verification_pass(
    pairs: list, provider: ModelProvider, profile: dict,
    pending_sensitive_hits: list, knowledge: dict,
    format_constraint: str, target_version_id: str, resolved_registers: dict,
) -> tuple[list, list]:
    """S2(이중 독립 검증) 패스. Claude와 GPT가 같은 원본을 동시에, 서로 뭘
    하는지 모른 채(앵커링 편향 방지) 독립적으로 검토한다 — 스페인어를 모르는
    검수자도 운영 가능해야 하므로, 두 모델의 일치 여부가 유일한 신뢰도
    신호다. 같은 줄을 둘 다 지적하면 합의 후보가 되고, 문구가 같은 뜻인지
    다시 양쪽에 교차 확인해 진짜 합의로 확정된 것만 자동 적용한다. 한쪽만
    지적했거나(불일치) 교차 확인에서 의견이 갈리면 적용하지 않고 원문을
    유지한 채 반대쪽 모델의 역번역만 참고용으로 붙인다.

    호출 시점의 pair.target.text는 이미 확정된 성별/격식이 반영된 상태다
    (run_pipeline_phase2가 이 함수를 부르기 전에 _apply_resolved_gender/
    _apply_resolved_formality로 먼저 처리해 둔다) — 그래서 이 함수는 더 이상
    resolved_gender/resolved_formality를 프롬프트에 실어보내지 않는다.
    "AI에게 반영해달라고 부탁"하는 대신 "이미 반영된 걸 건드리지 말라"고만
    시스템 프롬프트에서 지시한다(design §격식 지시가 무시됨 — 한 프롬프트가
    여러 일을 하다 부차 지시를 놓치는 문제를 원천적으로 없앰).
    호출 시점 filtered_pairs는 한국어·대상언어가 둘 다 있는 pair만 포함한다
    (아래 참고). 반환값은 (findings, warnings)."""
    # 한국어 원문이 없는 Segment(스페인어만 있는 반쪽짜리, design §스페인어만
    # 있는 경우)는 AI 이중검증에서 건너뛴다 — 비교할 원문이 없는 상태에서
    # "오역"을 판단하면 근거 없는 오탐만 만든다. 검수자가 직접 보고 판단해야
    # 하는 항목으로 남는다(검수 화면에 반쪽짜리로 표시됨).
    filtered_pairs = [p for p in pairs if p.target is not None and p.korean is not None]

    def _to_dict(p) -> dict:
        reg = resolved_registers.get(p.id) or {}
        return {
            "id": p.id,
            "korean_text": p.korean.text if p.korean else "",
            "target_text": p.target.text,
            "gender": reg.get("gender"),
            "formality": reg.get("formality"),
        }


    korean_text_by_id = {p.id: (p.korean.text if p.korean else "") for p in filtered_pairs}
    warnings: list = []

    skipped_without_korean = sum(1 for p in pairs if p.target is not None and p.korean is None)
    if skipped_without_korean:
        warnings.append({
            "stage": "AI 검증",
            "message": f"한국어 원문이 없어 AI 검증을 건너뛴 줄 {skipped_without_korean}건 — 검수 화면에서 직접 확인하세요.",
        })

    async def _verify_chunk(chunk: list) -> tuple[list, list]:
        chunk_dicts = [_to_dict(p) for p in chunk]
        return await asyncio.gather(
            _safe_call(
                provider.correct_primary(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint),
                "Claude 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
            _safe_call(
                provider.verify_and_refine(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint),
                "GPT 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
        )

    scene_chunks = await _split_into_scenes(
        provider, profile, filtered_pairs, target_version_id, warnings)
    chunk_results = await asyncio.gather(*[_verify_chunk(chunk) for chunk in scene_chunks])
    claude_corrections = _drop_malformed_corrections(
        [c for claude_chunk, _ in chunk_results for c in claude_chunk],
        "Claude 검증", target_version_id, warnings)
    gpt_corrections = _drop_malformed_corrections(
        [c for _, gpt_chunk in chunk_results for c in gpt_chunk],
        "GPT 검증", target_version_id, warnings)

    candidate_pairs, claude_only, gpt_only = _reconcile_dual_verification(
        claude_corrections, gpt_corrections)

    true_agreed, disputed_claude, disputed_gpt, equivalence_warnings = await _check_equivalence(
        candidate_pairs, korean_text_by_id, provider, profile, target_version_id)
    warnings.extend(equivalence_warnings)

    all_claude_only = claude_only + disputed_claude
    all_gpt_only = gpt_only + disputed_gpt

    # 단일 모델의 가벼운 어조/스타일 다듬기(unnatural_style, nuance_tone)는 과잉 교정 소음이므로 필터링한다.
    # 양쪽 모델 합의(true_agreed)이거나 중요 지적(sensitivity, mistranslation, locale_convention)인 항목만 유지한다.
    filtered_claude_only = [
        c for c in all_claude_only
        if c.get("category") not in ("unnatural_style", "nuance_tone")
    ]
    filtered_gpt_only = [
        c for c in all_gpt_only
        if c.get("category") not in ("unnatural_style", "nuance_tone")
    ]

    (backtranslation_by_id, original_backtranslation_by_id, not_improved,
     backtranslation_warnings) = await _back_translate_proposals(
        provider, profile, true_agreed, filtered_claude_only, filtered_gpt_only,
        target_version_id, pairs)
    warnings.extend(backtranslation_warnings)

    # 원문보다 나아지지 않았다고 판정된 교정은 여기서 폐기한다 — true_agreed여도
    # 예외 없음(두 모델이 같은 문구에 합의했다는 것과, 그 문구가 원문보다
    # 실제로 나은 것은 별개다). 폐기된 항목은 finding 자체가 안 생긴다.
    true_agreed = [c for c in true_agreed if (c["segment_id"], "gpt_authored") not in not_improved]
    filtered_claude_only = [
        c for c in filtered_claude_only
        if (c["segment_id"], "claude_authored") not in not_improved
    ]
    filtered_gpt_only = [
        c for c in filtered_gpt_only if (c["segment_id"], "gpt_authored") not in not_improved
    ]

    entries = [
        # true_agreed 항목의 corrected_text는 gpt_correction이다(agreed는
        # GPT 문구로 통일 — _check_equivalence 참고) — 그래서 역번역 출처도
        # "gpt_authored"다.
        *((c, "approved", "claude+gpt", True, "gpt_authored") for c in true_agreed),
        *((c, "pending", "claude", False, "claude_authored") for c in filtered_claude_only),
        *((c, "pending", "gpt", False, "gpt_authored") for c in filtered_gpt_only),
    ]

    await _reapply_resolved_gender_to_corrections(entries, profile, resolved_registers)

    pair_by_id = {p.id: p for p in pairs}
    findings: list = []
    for correction, status, model_label, applies, source in entries:
        pair = pair_by_id.get(correction["segment_id"])
        if pair is None or pair.target is None:
            continue
        if correction["corrected_text"] == pair.target.text:
            continue
        findings.append(await _make_dual_verification_finding(
            target_version_id, pair, correction, status, model_label, source,
            backtranslation_by_id, original_backtranslation_by_id, provider))
        if applies:
            pair.target.text = correction["corrected_text"]
    return findings, warnings


async def _run_final_safety_net(
    pairs: list, provider: ModelProvider, target_version_id: str,
    dual_verification_findings: list,
) -> tuple[list, list]:
    """S4 최종 안전망: 모든 교정이 끝난 텍스트를 기준으로 글자수·온점을 마지막
    으로 다시 검사한다 — GPT 패스가 문장을 늘리면서 새 위반을 만들 수 있어
    앞에서 한 번만 걸러서는 안 된다(design §핵심 설계 포인트: "앞에서 한 번만
    걸러선 안 됨"). 온점은 규칙 기반 자동보정이라 여기서도 LLM 없이 바로
    재적용한다. dual_verification_findings(S2 결과)를 넘겨서, 이미 자동
    승인된 finding과 같은 세그먼트면 새 카드를 또 만들지 않고 그 카드를
    갱신한다(검수자에게 같은 문장이 카드 두 개로 보이지 않게). 반환값은
    (final_ellipsis_violations, safety_net_findings)."""
    final_ellipsis_violations = check_ellipsis(pairs)
    final_fixed_by_segment = {v.segment_id: v.fixed_text for v in final_ellipsis_violations}
    for pair in pairs:
        if pair.id in final_fixed_by_segment:
            pair.target.text = final_fixed_by_segment[pair.id]

    line_length_violations = check_line_length(pairs)
    # Claude/GPT가 갈려서 아직 사람이 못 고른 pending 세그먼트는 여기서
    # 건너뛴다 — pair.target.text가 두 제안 중 어느 쪽도 반영 안 된 원문
    # 그대로라서(applies는 두 모델이 합의했을 때만 True — 위 entries 참고),
    # 이 시점에 만드는 축약 카드는 사람이 둘 중 하나를 고르는 순간 곧바로
    # 무의미해진다("같은 문장인데 카드가 3개" 문제, 사용자 재현). 사람이
    # 고른 뒤 최종 텍스트 기준 글자수는 export 직전 안전망(export.py의
    # safety_net_check)이 다시 검사하므로 놓치지 않는다.
    pending_segment_ids = {
        f.segment_id for f in dual_verification_findings if f.status == "pending"
    }
    line_length_violations = [
        v for v in line_length_violations if v.segment_id not in pending_segment_ids
    ]
    # reading_speed는 화면에 실제로 입혀서 확인하므로 여기서 체크하지 않음
    safety_net_findings = await shrink_violating_lines(
        pairs, line_length_violations, provider, target_version_id,
        existing_findings=dual_verification_findings)
    return final_ellipsis_violations, safety_net_findings


def _normalize_gender_for_ai(value: Optional[str]) -> Optional[str]:
    """resolved_gender_raw가 "not_applicable"(검수자가 "이건 사람 얘기가
    아니다"라고 답한 경우, 예: caro=비싸다)이면 AI 프롬프트에 실제 성별
    값처럼 전달하면 안 되므로 None으로 되돌린다 — male/female만 진짜 성별
    정보로 취급한다."""
    return value if value in ("male", "female") else None


def gender_groups_all_resolved(groups: Optional[list]) -> bool:
    """한 줄에 인물이 둘 이상이면 그룹별로 따로 답해야 한다 — 하나라도
    아직 gender가 없으면 그 줄은 아직 확정되지 않은 것이다."""
    return bool(groups) and all(g.get("gender") for g in groups)


def _gender_groups_for_ai(groups: Optional[list]) -> Optional[list]:
    """다인물 그룹의 확정된 성별을 AI 적용용 형태로 변환한다. not_applicable은
    실제 성별이 아니므로 gender를 None으로 남기되(_normalize_gender_for_ai와
    동일한 이유) 리스트에서 빼지는 않는다 — candidate_indices는 그룹 순서가
    아니라 이 문장을 spaCy로 다시 파싱했을 때의 후보 등장 순서를 직접
    가리키므로, 중간 그룹을 걸러내도 안전하다(resolve_gender_groups_in_texts
    참고). male/female이 하나도 없으면 None."""
    if not groups:
        return None
    result = [
        {"candidate_indices": g.get("candidate_indices") or [], "gender": _normalize_gender_for_ai(g.get("gender"))}
        for g in groups
    ]
    return result if any(g["gender"] for g in result) else None


def _build_resolved_registers(segment_resolutions: list) -> dict:
    """문법 필요성 판단이 이미 확정한 성별/격식만 모아 {segment_id: {"gender":..,
    "formality":.., "gender_groups":..}} 딕셔너리로 만든다 — 확정 안 된 줄은
    아예 안 들어가서 AI 검증이 여전히 아무것도 추측하지 않는다. gender_groups는
    한 줄에 인물이 둘 이상일 때만 채워지며, 인물(그룹)별로 확정된 성별을
    그 인물에 속한 단어에만 적용하기 위한 것이다(_apply_resolved_gender)."""
    registers: dict = {}
    for r in segment_resolutions:
        groups = r.get("resolved_gender_groups")
        if groups:
            if not gender_groups_all_resolved(groups):
                continue
            gender_groups = _gender_groups_for_ai(groups)
            formality = r.get("resolved_formality")
            if not (gender_groups or formality):
                continue
            entry = {"gender": None, "formality": formality}
            if gender_groups:
                entry["gender_groups"] = gender_groups
            registers[r["segment_id"]] = entry
            continue
        gender = _normalize_gender_for_ai(r.get("resolved_gender"))
        formality = r.get("resolved_formality")
        if not (gender or formality):
            continue
        registers[r["segment_id"]] = {"gender": gender, "formality": formality}
    return registers


def registers_need_confirmation(segment_resolutions: list) -> bool:
    """성별/격식 확인이 필요하다고 판단됐는데(*_check_needed) 아직 값이
    확정 안 된(resolved_* 없음) 줄이 하나라도 있으면 True — 있으면 AI
    검증(S2)을 시작하면 안 되고, 사람이 먼저 스텝퍼에서 답해야 한다."""
    return any(
        (r.get("gender_check_needed") and not r.get("resolved_gender")
         and not gender_groups_all_resolved(r.get("resolved_gender_groups")))
        or (r.get("formality_check_needed") and not r.get("resolved_formality"))
        for r in segment_resolutions
    )


def pairs_from_segments(segments: list, target_version_id: str) -> list:
    """확인 완료 후 S2(AI 검증)를 재개할 때, DB에 저장된 Segment 행에서
    pairs를 복원한다. save_phase1_result가 붙인 target_version_id 네임스페이스
    접두어를 다시 떼어 phase1이 쓰던 로컬 id(pair_1 등)로 되돌려야, 그 id를
    다시 저장할 때 기존 Segment.id와 정확히 일치한다."""
    prefix = f"{target_version_id}:"
    pairs = []
    for seg in segments:
        local_id = seg.id[len(prefix):] if seg.id.startswith(prefix) else seg.id
        korean = SegmentText(start=seg.start, end=seg.end, text=seg.korean_text) if seg.korean_text else None
        target = SegmentText(start=seg.start, end=seg.end, text=seg.target_text) if seg.target_text else None
        pairs.append(AlignedPair(id=local_id, korean=korean, target=target))
    return pairs


def resolved_registers_from_segments(segments: list, target_version_id: str) -> dict:
    """pairs_from_segments와 짝을 이룬다 — DB에 저장된 확정 성별/격식 값에서
    resolved_registers를 복원한다. resolved_gender_groups_raw(다인물 줄의
    인물별 답)가 있으면 그걸 우선 쓰고(_build_resolved_registers와 동일한
    형태로 변환), 없으면 기존처럼 단일 resolved_gender_raw를 쓴다."""
    prefix = f"{target_version_id}:"
    registers: dict = {}
    for seg in segments:
        local_id = seg.id[len(prefix):] if seg.id.startswith(prefix) else seg.id
        groups = seg.resolved_gender_groups_raw
        if groups:
            if not gender_groups_all_resolved(groups):
                continue
            gender_groups = _gender_groups_for_ai(groups)
            formality = seg.resolved_formality_raw
            if not (gender_groups or formality):
                continue
            entry = {"gender": None, "formality": formality}
            if gender_groups:
                entry["gender_groups"] = gender_groups
            registers[local_id] = entry
            continue
        gender = _normalize_gender_for_ai(seg.resolved_gender_raw)
        formality = seg.resolved_formality_raw
        if not (gender or formality):
            continue
        registers[local_id] = {"gender": gender, "formality": formality}
    return registers


async def run_pipeline_phase1(video_path: str, target_srt_path: str,
                               language: str, variant: str, target_version_id: str,
                               provider: ModelProvider,
                               cached_korean_segments: Optional[list] = None,
                               cached_video_proxy_path: Optional[str] = None,
                               korean_srt_path: Optional[str] = None,
                               known_gender_facts: Optional[dict] = None,
                               episode_gender_facts: Optional[dict] = None) -> dict:
    """S1(STT/정렬/사전·규칙 처리/문법 필요성 판단)만 실행한다. 성별/격식
    확인이 필요한 줄이 있으면 AI 검증(S2)은 여기서 시작하지 않는다 — 사람이
    확정한 뒤에야 정확한 검증이 가능하므로(design §AI 검증은 확정된 값을
    받고 시작해야 함), 호출자(background.py)가 registers_need_confirmation로
    판단해 필요 없을 때만 곧장 run_pipeline_phase2를 이어서 호출한다.

    원본 영상 삭제는 여기서 하지 않는다 — 이 함수가 반환한 뒤에도 아직 DB에
    아무것도 영속화되지 않은 상태이므로, 여기서 지우면 프로세스가 이 함수와
    호출자(background.py)의 저장 사이 어딘가에서 죽었을 때 원본도 없고 결과도
    없는 상태가 된다. 호출자가 결과를 실제로 커밋한 뒤에 지우도록
    `video_path`를 결과에 그대로 담아 돌려준다."""
    warnings: list = []
    target_segments = load_srt(target_srt_path)

    global_offset = 0.0
    video_offset_seconds = 0.0

    if korean_srt_path:
        # 한국어 SRT가 지정된 경우: STT 캐시보다 지문 정제 + 임베딩 DP 정렬이 우선한다.
        if cached_video_proxy_path:
            video_proxy_path = cached_video_proxy_path
        elif video_path:
            video_proxy_path = await asyncio.to_thread(generate_video_proxy, video_path)
        else:
            video_proxy_path = None

        raw_cues = load_srt(korean_srt_path)

        # 영상 동기화용 STT는 global_offset 계산과 독립적이라 먼저 돌려도
        # 되지만, raw_video_offset은 한국어 SRT "원본"(보정 전) 시계 기준이라
        # 최종 video_offset_seconds로 쓰려면 global_offset과 합쳐야 한다
        # (Segment.start는 한국어가 아니라 대상언어 SRT 시계 —
        # _detect_raw_video_sync_offset 독스트링 참고). 그래서 합산은
        # global_offset을 구한 뒤로 미룬다.
        # video_path(원본)가 아니라 방금 만든 video_proxy_path를 쓴다 —
        # 오디오 추출엔 원본 화질이 필요 없고(generate_video_proxy 참고),
        # 원본은 스토리지 정리로 나중에 지워질 수 있어(delete_original_video)
        # 원본에 의존하면 프록시가 멀쩡히 있어도 이 체크가 실패한다.
        raw_video_offset = None
        if video_proxy_path:
            raw_video_offset = await _detect_raw_video_sync_offset(
                provider, video_proxy_path, korean_srt_path, raw_cues, target_version_id, warnings)

        korean_cues = []
        for c in raw_cues:
            cleaned = _clean_text_for_embedding(c.text)
            # cleaned가 빈 문자열이면 지문/효과음뿐인 큐다(순수 대사가 없음) —
            # 원본을 그대로 되살리면 브라켓 지문이 그대로 노출되므로 버린다.
            if cleaned:
                korean_cues.append(SegmentText(start=c.start, end=c.end, text=cleaned))

        # 한국어 SRT 경로는 정렬용으로는 STT(실측 오디오 타이밍)를 안
        # 거치므로, 한국어 SRT와 대상언어 SRT가 각자 스스로 적어놓은
        # 타임코드를 그대로 믿는다 — 둘 사이에 상수 오프셋이 있어도(예:
        # 서로 다른 인트로 길이 기준으로 작업됨) 못 잡아낸다. 한국어 SRT는
        # 검증된 원문으로 간주하므로(다른 분기의 STT와 같은 역할), 이걸
        # 기준 삼아 대상언어 SRT와 비교해 같은 방식으로 오프셋을 찾는다 —
        # 새로 오디오를 분석할 필요 없이 이미 가진 두 SRT의 타임코드만으로
        # 계산된다.
        global_offset = detect_global_offset(korean_cues, target_segments)
        if global_offset:
            korean_cues = [
                SegmentText(start=c.start + global_offset, end=c.end + global_offset, text=c.text)
                for c in korean_cues
            ]
            warnings.append({
                "stage": "타임코드 자동 보정",
                "message": f"한국어 SRT와 대상언어 SRT 사이 {global_offset:+.1f}초 오프셋을 감지해 자동 보정했습니다.",
            })

        # 대상언어 SRT 시계 ≈ 한국어 SRT 원본 시계 + global_offset이므로,
        # 영상 재생 seek용 오프셋은 raw_video_offset을 그대로 쓰지 않고
        # global_offset과 합친다. 탐지 실패(None)했으면 video-specific
        # 정보가 없다는 뜻이니, "대상언어 시계 = 실제 영상 시계"라고
        # 가정하는(오늘 이 수정 이전과 동일한) global_offset을 그대로
        # 폴백으로 쓴다 — 조용히 아예 0으로 떨어지진 않는다.
        if raw_video_offset is not None:
            video_offset_seconds = global_offset - raw_video_offset
            if abs(video_offset_seconds) > 0.5:
                warnings.append({
                    "stage": "영상 동기화",
                    "message": f"한국어 SRT와 실제 영상 사이 {video_offset_seconds:+.1f}초 오프셋을 감지해 영상 재생을 자동 보정했습니다.",
                })
        else:
            video_offset_seconds = global_offset

        korean_raw = [{"start": c.start, "end": c.end, "text": c.text} for c in korean_cues]
        pairs = await align_by_embedding_dp(
            korean_cues, target_segments, provider, korean_raw_cues=raw_cues)

    elif cached_korean_segments is not None and cached_video_proxy_path is not None:
        # Episode 단위 캐시 재사용 (한국어 SRT가 없는 STT 전용 경로)
        korean_raw = cached_korean_segments
        video_proxy_path = cached_video_proxy_path
        korean_words = [SegmentText(**s) for s in korean_raw]

        # 이 분기의 korean_words는 이미 실측 STT 타이밍이라, 대상언어 SRT와의
        # 오프셋이 그대로 실제 영상과의 오프셋이기도 하다(korean_srt_path
        # 분기와 달리 별도 탐지가 필요 없음).
        global_offset = detect_global_offset(korean_words, target_segments)
        video_offset_seconds = global_offset
        if global_offset:
            korean_raw = [
                {**s, "start": s["start"] + global_offset, "end": s["end"] + global_offset}
                for s in korean_raw
            ]
            korean_words = [SegmentText(**s) for s in korean_raw]
            warnings.append({
                "stage": "타임코드 자동 보정",
                "message": f"한국어 STT와 대상언어 SRT 사이 {global_offset:+.1f}초 오프셋을 감지해 자동 보정했습니다.",
            })
        pairs = align(korean_words, target_segments)


    else:
        # 한국어 SRT가 없는 경우: 기존 오디오 STT 실행 후 단어 단위 정렬을 수행한다.
        korean_raw, video_proxy_path = await _run_stt_and_proxy(provider, video_path)
        korean_words = [SegmentText(**s) for s in korean_raw]

        global_offset = detect_global_offset(korean_words, target_segments)
        video_offset_seconds = global_offset
        if global_offset:
            korean_raw = [
                {**s, "start": s["start"] + global_offset, "end": s["end"] + global_offset}
                for s in korean_raw
            ]
            korean_words = [SegmentText(**s) for s in korean_raw]
            warnings.append({
                "stage": "타임코드 자동 보정",
                "message": f"한국어 STT와 대상언어 SRT 사이 {global_offset:+.1f}초 오프셋을 감지해 자동 보정했습니다.",
            })
        pairs = align(korean_words, target_segments)


    # 온점 자동보정은 다른 모든 단계보다 먼저 적용한다 — 이후 단계가 보정된
    # 텍스트를 기준으로 작업하도록.
    ellipsis_violations = check_ellipsis(pairs)
    fixed_by_segment = {v.segment_id: v.fixed_text for v in ellipsis_violations}
    for pair in pairs:
        if pair.id in fixed_by_segment:
            pair.target.text = fixed_by_segment[pair.id]

    profile = load_profile(language, variant)
    sensitive_terms = load_sensitive_terms()
    glossary = load_glossary()
    cta_patterns = load_cta_patterns()
    profanity_dictionary = load_profanity_dictionary()

    pretreatment = run_pretreatment(
        pairs, glossary, cta_patterns, profanity_dictionary, sensitive_terms,
        target_version_id,
    )
    pairs = pretreatment.pairs

    segment_resolutions, grammar_warnings = await _run_grammar_necessity_check(
        pairs, profile, provider, target_version_id, known_gender_facts, episode_gender_facts,
    )
    warnings.extend(grammar_warnings)

    await _gloss_gender_words(
        segment_resolutions, pairs, provider, profile, target_version_id, warnings)

    return {
        "pairs": pairs,
        "format_violations": ellipsis_violations,
        "segment_resolutions": segment_resolutions,
        "video_path": video_path,
        "video_proxy_path": video_proxy_path,
        "video_offset_seconds": video_offset_seconds,
        "korean_segments_raw": korean_raw,
        "warnings": warnings,
        "findings": pretreatment.findings,
        "pending_sensitive_hits": pretreatment.pending_sensitive_hits,
    }


async def _apply_resolved_gender(pairs: list, profile: dict, resolved_registers: dict) -> None:
    """확정된 성별을 파이썬이 직접 문장에 반영한다(제자리 수정) — AI에게
    "반영해달라"고 부탁하지 않는다. 문법 규칙(형용사 성별 어미)은 결정론적
    으로 처리 가능하니, 그래야 AI가 이 지시를 놓치는 문제가 원천적으로
    없어진다. 한 줄에 인물이 둘 이상이면(gender_groups) 인물별로 확정된
    성별을 그 인물의 단어에만 적용한다 — 단일 인물 줄과 다인물 줄은 서로
    다른 함수(resolve_gender_in_texts/resolve_gender_groups_in_texts)로
    처리하지만 결과는 같은 딕셔너리에 합쳐 pair.target.text에 반영한다.
    spaCy 분석은 CPU 바운드 동기 작업이라 asyncio.to_thread로 감싼다
    (check_grammar_necessity 호출부와 동일한 이유)."""
    single_items = [
        {"id": p.id, "text": p.target.text, "gender": resolved_registers[p.id]["gender"]}
        for p in pairs
        if p.target is not None and resolved_registers.get(p.id, {}).get("gender")
    ]
    group_items = [
        {"id": p.id, "text": p.target.text, "groups": resolved_registers[p.id]["gender_groups"]}
        for p in pairs
        if p.target is not None and resolved_registers.get(p.id, {}).get("gender_groups")
    ]
    if not single_items and not group_items:
        return
    fixed_by_id: dict = {}
    if single_items:
        fixed_by_id.update(await asyncio.to_thread(
            resolve_gender_in_texts, single_items, profile.get("language")))
    if group_items:
        fixed_by_id.update(await asyncio.to_thread(
            resolve_gender_groups_in_texts, group_items, profile.get("language")))
    for pair in pairs:
        if pair.id in fixed_by_id:
            pair.target.text = fixed_by_id[pair.id]


async def _apply_resolved_formality(
    pairs: list, provider: ModelProvider, profile: dict, resolved_registers: dict,
) -> None:
    """확정된 격식만 반영하는 전담 LLM 호출로 문장을 고친다(제자리 수정) —
    오역/뉘앙스 등 다른 검증과 한 프롬프트에 섞이면 이 지시를 놓치는 문제가
    있었다(design §격식 지시가 무시됨). 이 결과가 이후 이중검증(S2)의 새
    기준 텍스트가 된다."""
    items = [
        {"id": p.id, "target_text": p.target.text, "formality": resolved_registers[p.id]["formality"]}
        for p in pairs
        if p.target is not None and resolved_registers.get(p.id, {}).get("formality")
    ]
    if not items:
        return
    results = await provider.apply_formality(items, profile)
    corrected_by_id = {r["id"]: r["corrected_text"] for r in results}
    for pair in pairs:
        if pair.id in corrected_by_id:
            pair.target.text = corrected_by_id[pair.id]


async def run_pipeline_phase2(pairs: list, provider: ModelProvider, profile: dict,
                               knowledge: dict, pending_sensitive_hits: list,
                               target_version_id: str, resolved_registers: dict) -> dict:
    """S2(Claude/GPT 이중 독립 검증) + S4(최종 안전망). 성별/격식 확인이
    필요한 줄이 모두 확정된 뒤에만 호출돼야 한다(run_pipeline_phase1의
    registers_need_confirmation이 False일 때, 또는 사람이 스텝퍼에서 답을
    마친 뒤). resolved_registers는 확정된 성별/격식({segment_id: {"gender":..,
    "formality":..}})이다 — 이중검증을 시작하기 전에 먼저 이 값을 pair.target
    .text에 실제로 반영한다(성별은 파이썬으로 결정론적으로, 격식은 격식만
    전담하는 별도 LLM 호출로) — 그래야 이중검증이 "이미 맞는 문장"을 기준
    으로 다른 문제만 찾으면 된다(design §AI에게 반영해달라 부탁하지 말고
    파이썬/전담 호출이 먼저 확정)."""
    await _apply_resolved_gender(pairs, profile, resolved_registers)
    await _apply_resolved_formality(pairs, provider, profile, resolved_registers)

    format_constraint = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."

    # 이미 확정된 성별/격식을 pairs에 추가 (verify_and_refine의 5단계 프롬프트에서 참고하도록)
    for pair in pairs:
        reg = resolved_registers.get(pair.id) or {}
        pair.gender = reg.get("gender")
        pair.formality = reg.get("formality")

    dual_verification_findings, dual_verification_warnings = await _run_dual_verification_pass(
        pairs, provider, profile,
        pending_sensitive_hits, knowledge, format_constraint,
        target_version_id, resolved_registers,
    )

    final_ellipsis_violations, safety_net_findings = await _run_final_safety_net(
        pairs, provider, target_version_id, dual_verification_findings,
    )
    # line_length_violations는 여기 담아 반환하지 않는다 — shrink_violating_lines가
    # 이미 텍스트를 줄이고 그 결과를 category="formatting" Finding으로 반환했다.
    # 그런데도 여기 다시 담으면 repositories.py가 "이미 줄어든 뒤" 텍스트를
    # original_text로 삼아 별도의 pending formatting finding을 하나 더 만든다 —
    # 검수자에게 사실과 다른(더 이상 위반이 아닌) 원문을 보여주는 중복 레코드다.
    # 온점 위반은 safety_net 같은 별도 Finding 생성 경로가 없는(규칙 기반
    # 자동보정이 전부인) 경우라 여기서 그대로 반환해야 한다.
    return {
        "pairs": pairs,
        "format_violations": final_ellipsis_violations,
        "warnings": dual_verification_warnings,
        "findings": dual_verification_findings + safety_net_findings,
    }


async def run_pipeline(video_path: str, target_srt_path: str,
                        language: str, variant: str, target_version_id: str,
                        provider: ModelProvider,
                        cached_korean_segments: Optional[list] = None,
                        cached_video_proxy_path: Optional[str] = None,
                        korean_srt_path: Optional[str] = None,
                        known_gender_facts: Optional[dict] = None,
                        episode_gender_facts: Optional[dict] = None) -> dict:
    """phase1 + phase2를 곧장 이어서 실행하는 편의 래퍼 — 성별/격식 확인이
    필요한 줄이 있어도 기다리지 않고 바로 phase2까지 실행한다. 실제 운영
    경로(background.py)는 이 함수를 쓰지 않는다 — registers_need_confirmation
    으로 확인이 필요한지부터 판단해야 하므로 phase1/phase2를 항상 따로
    호출한다. 이 래퍼는 그 판단이 필요 없는 테스트/스크립트 편의용이다."""
    profile = load_profile(language, variant)
    knowledge = load_knowledge()
    phase1 = await run_pipeline_phase1(
        video_path, target_srt_path, language, variant, target_version_id, provider,
        cached_korean_segments, cached_video_proxy_path, korean_srt_path,
        known_gender_facts, episode_gender_facts,
    )
    resolved_registers = _build_resolved_registers(phase1["segment_resolutions"])
    phase2 = await run_pipeline_phase2(
        phase1["pairs"], provider, profile, knowledge, phase1["pending_sensitive_hits"],
        target_version_id, resolved_registers,
    )
    return {
        **phase1,
        "pairs": phase2["pairs"],
        "format_violations": phase1["format_violations"] + phase2["format_violations"],
        "warnings": phase1["warnings"] + phase2["warnings"],
        "findings": phase1["findings"] + phase2["findings"],
    }
