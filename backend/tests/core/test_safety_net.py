import pytest
from app.core.safety_net import shrink_violating_lines
from app.schemas import AlignedPair, SegmentText, FormatViolation
from app.providers.mock import MockProvider


def _pair(id_, text):
    return AlignedPair(id=id_, target=SegmentText(start=0.0, end=1.0, text=text))


@pytest.mark.asyncio
async def test_no_violations_skips_llm_call_entirely():
    pairs = [_pair("p1", "짧은 줄")]
    findings = await shrink_violating_lines(pairs, [], MockProvider(), "tv1")
    assert findings == []
    assert pairs[0].target.text == "짧은 줄"


@pytest.mark.asyncio
async def test_violation_shrinks_text_and_updates_pair_in_place():
    long_text = "가" * 60
    pairs = [_pair("p1", long_text)]
    violations = [FormatViolation(segment_id="p1", rule="line_length", detail="60자")]
    findings = await shrink_violating_lines(pairs, violations, MockProvider(), "tv1")
    assert len(findings) == 1
    assert findings[0].model == "안전망"
    assert findings[0].status == "approved"
    assert pairs[0].target.text == findings[0].suggested_text
    assert len(pairs[0].target.text) <= 50


@pytest.mark.asyncio
async def test_violation_for_unknown_segment_id_is_skipped():
    pairs = [_pair("p1", "짧은 줄")]
    violations = [FormatViolation(segment_id="does-not-exist", rule="line_length", detail="x")]
    findings = await shrink_violating_lines(pairs, violations, MockProvider(), "tv1")
    assert findings == []
