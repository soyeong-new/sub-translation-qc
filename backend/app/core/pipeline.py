"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

import asyncio
import logging
from typing import Optional
from app.providers.base import ModelProvider
from app.core.ingest import load_srt, extract_audio, generate_video_proxy, delete_original_video
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
                        prior_relationships: Optional[list] = None) -> dict:
    """design §전체 파이프라인의 오케스트레이터. S1(사전/규칙) → S2(Claude 1차)
    → S3(GPT 2차) → S4(최종 안전망) 순서로 순차 실행하며, 각 단계의 diff가
    findings가 된다. 오디오/영상 프록시는 STT 직후 한 번만 생성하고 원본
    영상은 그 자리에서 삭제한다."""
    target_segments = load_srt(target_srt_path)

    wav_path = extract_audio(video_path)
    # STT(네트워크 호출)와 영상 저화질 프록시 생성(로컬 ffmpeg, CPU 바운드)은
    # 서로 결과를 주고받지 않는 독립적인 작업이라 동시에 실행한다 — STT를
    # 기다리는 동안 영상 트랜스코딩도 같이 진행되어 전체 대기 시간이 줄어든다.
    # generate_video_proxy는 동기 함수라 asyncio.to_thread로 감싸 이벤트
    # 루프를 막지 않게 한다.
    korean_raw, video_proxy_path = await asyncio.gather(
        provider.transcribe(wav_path),
        asyncio.to_thread(generate_video_proxy, video_path),
    )
    # 프록시 생성이 끝난 뒤에만 원본을 지운다 — 프록시가 원본을 입력으로 삼으므로
    # 순서가 바뀌면 안 된다.
    delete_original_video(video_path)
    korean_segments = [SegmentText(**s) for s in korean_raw]

    pairs = align(korean_segments, target_segments)

    # 온점 자동보정은 다른 모든 단계보다 먼저 적용한다 — 이후 단계가 보정된
    # 텍스트를 기준으로 작업하도록.
    ellipsis_violations = check_ellipsis(pairs)
    fixed_by_segment = {v.segment_id: v.fixed_text for v in ellipsis_violations}
    for pair in pairs:
        if pair.id in fixed_by_segment:
            pair.target.text = fixed_by_segment[pair.id]

    # GPT 2차의 "원본 대조 안전장치"용으로, 온점 보정까지만 적용된 상태의
    # 텍스트를 원본으로 기록해 둔다.
    original_target_by_id = {
        p.id: p.target.text for p in pairs if p.target is not None
    }

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

    registry = await build_registry(pairs, profile, provider)
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
    format_violations = ellipsis_violations + final_ellipsis_violations + line_length_violations

    return {
        "pairs": pairs,
        "format_violations": format_violations,
        "characters": characters,
        "relationships": relationships,
        "gender_questions": gender_questions,
        "register_questions": register_questions,
        "video_proxy_path": video_proxy_path,
        "findings": (
            pretreatment.findings + claude_findings + gpt_findings + safety_net_findings
        ),
    }
