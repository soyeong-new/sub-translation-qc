import pytest
from typing import List
from app.schemas import AlignedPair, SegmentText
from app.core.translation_review import run_translation_review
from app.providers.base import ModelProvider
from app.providers.mock import MockProvider


class MalformedFindingsProvider(ModelProvider):
    """Test provider that returns both valid and malformed findings."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return []

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        return {"characters": [], "relationships": []}

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        """Return one valid finding and one with unknown segment_id."""
        return [
            {
                "segment_id": "p1", "category": "translation",
                "description": "테스트 오역",
                "suggested_text": "texto corregido",
                "confidence": 0.9,
            },
            {
                # Malformed: unknown segment_id
                "segment_id": "unknown_id", "category": "translation",
                "description": "테스트 오역",
                "suggested_text": "texto corregido",
                "confidence": 0.9,
            },
        ]

    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        return []


class MissingFieldProvider(ModelProvider):
    """Test provider that returns findings with missing required fields."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return []

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        return {"characters": [], "relationships": []}

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        """Return one valid finding and one missing a required field."""
        return [
            {
                "segment_id": "p1", "category": "translation",
                "description": "테스트 오역",
                "suggested_text": "texto corregido",
                "confidence": 0.9,
            },
            {
                # Malformed: missing 'confidence' field
                "segment_id": "p1", "category": "translation",
                "description": "테스트 오역",
                "suggested_text": "texto corregido",
            },
        ]

    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        return []


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


@pytest.mark.asyncio
async def test_run_translation_review_skips_unknown_segment_id():
    """Test that malformed findings with unknown segment_id are skipped."""
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="text"))]
    profile = {"naturalness_check": {"llm_instruction": "..."}}
    findings = await run_translation_review(pairs, profile, "지식베이스", MalformedFindingsProvider(), "tv1")
    # Should only have the valid finding (with segment_id="p1"), not the malformed one
    assert len(findings) == 1
    assert findings[0].segment_id == "p1"


@pytest.mark.asyncio
async def test_run_translation_review_skips_missing_required_field():
    """Test that malformed findings with missing required fields are skipped."""
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="text"))]
    profile = {"naturalness_check": {"llm_instruction": "..."}}
    findings = await run_translation_review(pairs, profile, "지식베이스", MissingFieldProvider(), "tv1")
    # Should only have the valid finding, not the one missing 'confidence'
    assert len(findings) == 1
    assert findings[0].confidence == 0.9
