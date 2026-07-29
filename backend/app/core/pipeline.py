"""STT부터 검수용 findings 생성까지 전체 QC 파이프라인을 조율하는 오케스트레이터."""

from typing import Optional
from app.providers.base import ModelProvider
from app.core.ingest import load_srt, extract_audio
from app.core.alignment import align
from app.core.format_rules import check_line_length, check_ellipsis, fix_ellipsis
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge, load_sensitive_terms
from app.core.character_registry import build_registry
from app.core.consistency_check import find_gender_conflicts, find_register_conflicts
from app.core.translation_review import run_translation_review
from app.core.sensitivity_check import run_sensitivity_check


async def run_pipeline(korean_audio_path: str, target_srt_path: str,
                        language: str, variant: str, target_version_id: str,
                        provider: ModelProvider,
                        prior_characters: Optional[list] = None,
                        prior_relationships: Optional[list] = None) -> dict:
    """design §4 전체 파이프라인의 오케스트레이터. 오디오는 STT에서 딱 한 번만
    쓰이고, 이후 전부 텍스트 기반이다."""
    target_segments = load_srt(target_srt_path)

    wav_path = extract_audio(korean_audio_path)
    korean_raw = await provider.transcribe(wav_path)
    from app.schemas import SegmentText
    korean_segments = [SegmentText(**s) for s in korean_raw]

    pairs = align(korean_segments, target_segments)

    # 온점 자동보정은 원본 텍스트 기준으로 먼저 감지·기록한 뒤 적용한다.
    # 그래야 무엇이 왜 바뀌었는지 로그(향후 learned_examples)에 남길 수 있다.
    ellipsis_violations = check_ellipsis(pairs)
    fixed_by_segment = {v.segment_id: v.fixed_text for v in ellipsis_violations}
    for pair in pairs:
        if pair.id in fixed_by_segment:
            pair.target.text = fixed_by_segment[pair.id]
    # 최초 줄길이 체크(design §5-1의 1번 지점)는 온점 보정이 끝난 텍스트 기준으로 수행한다.
    line_length_violations = check_line_length(pairs)
    format_violations = ellipsis_violations + line_length_violations

    profile = load_profile(language, variant)
    knowledge = load_knowledge()
    sensitive_terms = load_sensitive_terms()

    registry = await build_registry(pairs, profile, provider)
    characters = prior_characters if prior_characters is not None else registry["characters"]
    relationships = prior_relationships if prior_relationships is not None else registry["relationships"]
    gender_questions = find_gender_conflicts(characters)
    register_questions = find_register_conflicts(relationships)

    translation_findings = await run_translation_review(
        pairs, profile, knowledge, provider, target_version_id)
    sensitivity_findings = await run_sensitivity_check(
        pairs, sensitive_terms, provider, target_version_id)

    return {
        "pairs": pairs,
        "format_violations": format_violations,
        "characters": characters,
        "relationships": relationships,
        "gender_questions": gender_questions,
        "register_questions": register_questions,
        "findings": translation_findings + sensitivity_findings,
    }
