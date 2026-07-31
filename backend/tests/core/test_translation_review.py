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


class InvalidCategoryProvider(ModelProvider):
    """Test provider that returns findings with invalid category values."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return []

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        return {"characters": [], "relationships": []}

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        """Return one valid finding and one with invalid category."""
        return [
            {
                "segment_id": "p1", "category": "translation",
                "description": "테스트 오역",
                "suggested_text": "texto corregido",
                "confidence": 0.9,
            },
            {
                # Malformed: invalid category value (not in Literal["gender", "register", "translation", ...])
                "segment_id": "p1", "category": "mistranslation",
                "description": "테스트 오류",
                "suggested_text": "texto error",
                "confidence": 0.8,
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


@pytest.mark.asyncio
async def test_run_translation_review_skips_invalid_category():
    """Test that malformed findings with invalid category values (ValidationError) are skipped."""
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="text"))]
    profile = {"naturalness_check": {"llm_instruction": "..."}}
    findings = await run_translation_review(pairs, profile, "지식베이스", InvalidCategoryProvider(), "tv1")
    # Should only have the valid finding (category="translation"), not the one with invalid category
    assert len(findings) == 1
    assert findings[0].category == "translation"


class TwoModelProvider(ModelProvider):
    """같은 세그먼트에 대해 서로 다른 model 태그를 단 finding 두 개를 돌려준다."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return []

    async def analyze_characters(self, pairs, profile) -> dict:
        return {"characters": [], "relationships": []}

    async def review_translation(self, pairs, knowledge, profile, format_constraint) -> List[dict]:
        return [
            {"segment_id": "p1", "category": "translation", "description": "클로드 지적",
             "suggested_text": "texto A", "confidence": 0.9, "model": "claude"},
            {"segment_id": "p1", "category": "translation", "description": "GPT 지적",
             "suggested_text": "texto B", "confidence": 0.9, "model": "gpt"},
        ]

    async def check_sensitivity(self, pairs, term_hits) -> List[dict]:
        return []


@pytest.mark.asyncio
async def test_findings_from_different_models_get_distinct_ids():
    """같은 segment_id+category라도 model이 다르면 id가 달라야 DB에 저장할 때
    서로 덮어쓰지 않는다 (ensemble 병합의 전제조건)."""
    pairs = [AlignedPair(
        id="p1",
        korean=SegmentText(start=0, end=1, text="원문"),
        target=SegmentText(start=0, end=1, text="texto original"),
    )]
    findings = await run_translation_review(pairs, {}, "", TwoModelProvider(), "tv1")
    assert len(findings) == 2
    ids = {f.id for f in findings}
    assert len(ids) == 2
    models = {f.model for f in findings}
    assert models == {"claude", "gpt"}


class SameModelDuplicateCategoryProvider(ModelProvider):
    """한 모델이 같은 segment_id+category에 대해 finding을 두 개 돌려준다
    (예: 한 줄에서 오역과 로컬라이제이션 뉘앙스를 각각 지적)."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return []

    async def analyze_characters(self, pairs, profile) -> dict:
        return {"characters": [], "relationships": []}

    async def review_translation(self, pairs, knowledge, profile, format_constraint) -> List[dict]:
        return [
            {"segment_id": "p1", "category": "translation", "description": "첫 번째 지적",
             "suggested_text": "texto A", "confidence": 0.9},
            {"segment_id": "p1", "category": "translation", "description": "두 번째 지적",
             "suggested_text": "texto B", "confidence": 0.8},
        ]

    async def check_sensitivity(self, pairs, term_hits) -> List[dict]:
        return []


@pytest.mark.asyncio
async def test_duplicate_findings_same_segment_and_category_get_distinct_ids():
    """단일 모델이라도 같은 segment_id+category에 대해 finding을 두 개 이상
    돌려줄 수 있다. id가 finding_{segment_id}_{category}{model}로만 결정되면
    두 번째 항목이 첫 번째와 동일한 PK가 되어 저장 시 IntegrityError로 job
    전체가 실패한다 (회귀 방지: Finding 3)."""
    pairs = [AlignedPair(
        id="p1",
        korean=SegmentText(start=0, end=1, text="원문"),
        target=SegmentText(start=0, end=1, text="texto original"),
    )]
    findings = await run_translation_review(pairs, {}, "", SameModelDuplicateCategoryProvider(), "tv1")
    assert len(findings) == 2
    ids = {f.id for f in findings}
    assert len(ids) == 2
    assert findings[0].id == "finding_p1_translation"
    assert findings[1].id == "finding_p1_translation_2"


@pytest.mark.asyncio
async def test_finding_without_model_keeps_original_id_format():
    """model 키가 없는(단일 모델) 응답은 기존 id 형식을 그대로 유지해야
    한다 — 기존 동작에 대한 회귀 방지."""
    pairs = [AlignedPair(
        id="p1",
        korean=SegmentText(start=0, end=1, text="원문"),
        target=SegmentText(start=0, end=1, text="BAD_TRANSLATION"),
    )]
    findings = await run_translation_review(pairs, {}, "", MockProvider(), "tv1")
    assert findings[0].id == "finding_p1_translation"
    assert findings[0].model is None
