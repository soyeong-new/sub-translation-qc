import pytest
from app.schemas import AlignedPair, SegmentText
from app.core.sensitivity_check import run_sensitivity_check
from app.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_dictionary_hit_triggers_llm_precision_check():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="qué mierda pasa"))]
    findings = await run_sensitivity_check(pairs, ["mierda"], MockProvider(), "tv1")
    assert len(findings) == 1
    assert findings[0].category == "sensitivity"
    assert findings[0].target_version_id == "tv1"


@pytest.mark.asyncio
async def test_no_dictionary_hit_skips_llm_call():
    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="buenos días"))]
    findings = await run_sensitivity_check(pairs, ["mierda"], MockProvider(), "tv1")
    assert findings == []


@pytest.mark.asyncio
async def test_malformed_sensitivity_response_skips_invalid_item():
    """Test that malformed LLM responses (unknown segment_id or invalid fields)
    are skipped without crashing the whole function. One valid item and one
    malformed item should yield only the valid Finding."""
    pairs = [
        AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="qué mierda pasa")),
        AlignedPair(id="p2", target=SegmentText(start=1, end=2, text="esto es malo")),
    ]
    # Create a custom provider that returns both valid and malformed responses
    class MalformedMockProvider(MockProvider):
        async def check_sensitivity(self, pair_dicts, hits):
            # Return one valid item and one with unknown segment_id
            return [
                {
                    "segment_id": "p1",
                    "description": "Contiene un insulto",
                },
                {
                    "segment_id": "unknown_id",  # This will cause KeyError
                    "description": "Another issue",
                },
            ]

    findings = await run_sensitivity_check(pairs, ["mierda", "malo"], MalformedMockProvider(), "tv1")
    # Should only have 1 finding (the valid one), not crash
    assert len(findings) == 1
    assert findings[0].segment_id == "p1"
    assert findings[0].category == "sensitivity"


@pytest.mark.asyncio
async def test_targetless_pairs_do_not_crash():
    """Test that pairs with target=None don't crash the function.
    They are excluded from pair_dicts before calling the provider,
    and if somehow included in the response, the None-guard in
    original_text construction prevents AttributeError."""
    pairs = [
        AlignedPair(id="p1", korean=SegmentText(start=0, end=1, text="안녕"), target=None),
        AlignedPair(id="p2", target=SegmentText(start=1, end=2, text="qué mierda pasa")),
    ]
    # Mock provider that might return a finding for the targetless pair
    class TargetlessResponseMockProvider(MockProvider):
        async def check_sensitivity(self, pair_dicts, hits):
            # Return a finding (even though p1 shouldn't be in pair_dicts)
            return [
                {
                    "segment_id": "p1",
                    "description": "Some issue",
                },
                {
                    "segment_id": "p2",
                    "description": "Contiene un insulto",
                },
            ]

    findings = await run_sensitivity_check(pairs, ["mierda"], TargetlessResponseMockProvider(), "tv1")
    # Should successfully process without crashing on pair.target.text when target is None
    # p1 will have None-guarded original_text, p2 will be normal
    assert len(findings) == 2
    assert findings[0].segment_id == "p1"
    assert findings[0].original_text == ""  # None-guarded
    assert findings[1].segment_id == "p2"
    assert findings[1].original_text == "qué mierda pasa"


@pytest.mark.asyncio
async def test_duplicate_sensitivity_findings_same_segment_get_distinct_ids():
    """단일 모델이 같은 segment_id에 대해 sensitivity finding을 두 개 이상
    돌려줄 수 있다. id가 finding_{segment_id}_sensitivity{model}로만
    결정되면 두 번째 항목이 첫 번째와 동일한 PK가 되어 저장 시 IntegrityError로
    job 전체가 실패한다 (회귀 방지: Finding 3)."""
    class DuplicateProvider(MockProvider):
        async def check_sensitivity(self, pair_dicts, hits):
            return [
                {"segment_id": "p1", "description": "첫 번째 지적"},
                {"segment_id": "p1", "description": "두 번째 지적"},
            ]

    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="qué mierda pasa"))]
    findings = await run_sensitivity_check(pairs, ["mierda"], DuplicateProvider(), "tv1")
    assert len(findings) == 2
    ids = {f.id for f in findings}
    assert len(ids) == 2
    assert findings[0].id == "finding_p1_sensitivity"
    assert findings[1].id == "finding_p1_sensitivity_2"


@pytest.mark.asyncio
async def test_sensitivity_finding_with_model_gets_suffixed_id():
    class TaggedProvider(MockProvider):
        async def check_sensitivity(self, pair_dicts, hits):
            return [{"segment_id": "p1", "description": "민감어 확인", "model": "gpt"}]

    pairs = [AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="qué mierda pasa"))]
    findings = await run_sensitivity_check(pairs, ["mierda"], TaggedProvider(), "tv1")
    assert findings[0].id == "finding_p1_sensitivity_gpt"
    assert findings[0].model == "gpt"
