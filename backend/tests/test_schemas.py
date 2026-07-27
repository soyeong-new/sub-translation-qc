from app.schemas import SegmentText, AlignedPair, Finding


def test_segment_text_requires_times_and_text():
    seg = SegmentText(start=1.0, end=2.5, text="hola")
    assert seg.end > seg.start


def test_aligned_pair_allows_missing_korean_or_target():
    pair = AlignedPair(id="p1", korean=None, target=None)
    assert pair.korean is None and pair.target is None


def test_finding_category_must_be_known_value():
    f = Finding(
        id="f1", target_version_id="tv1", segment_id="p1",
        category="translation", description="근거", original_text="a",
        suggested_text="b", confidence=0.8, source="llm", status="pending",
    )
    assert f.status == "pending"
