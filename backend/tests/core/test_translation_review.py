import pytest
from app.schemas import AlignedPair, SegmentText
from app.core.translation_review import run_translation_review
from app.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_run_translation_review_wraps_provider_findings_as_finding_models():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="BAD_TRANSLATION aquí"))]
    profile = {"naturalness_check": {"llm_instruction": "..."}}
    findings = await run_translation_review(pairs, profile, "지식베이스", MockProvider(), "tv1")
    assert len(findings) == 1
    assert findings[0].category == "translation"
    assert findings[0].target_version_id == "tv1"
    assert findings[0].source == "llm"


@pytest.mark.asyncio
async def test_run_translation_review_returns_empty_for_clean_text():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="Hola, buen día"))]
    profile = {"naturalness_check": {"llm_instruction": "..."}}
    findings = await run_translation_review(pairs, profile, "지식베이스", MockProvider(), "tv1")
    assert findings == []
