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
