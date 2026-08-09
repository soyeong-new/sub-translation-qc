from app.schemas import SegmentText
from app.core.alignment import align


def test_align_matches_overlapping_segments():
    korean = [SegmentText(start=0.0, end=2.0, text="안녕")]
    target = [SegmentText(start=0.1, end=2.1, text="Hola")]
    pairs = align(korean, target)
    assert len(pairs) == 1
    assert pairs[0].korean.text == "안녕"
    assert pairs[0].target.text == "Hola"


def test_align_produces_unmatched_pair_when_no_overlap():
    korean = [SegmentText(start=0.0, end=1.0, text="안녕")]
    target = [SegmentText(start=50.0, end=51.0, text="Hola")]
    pairs = align(korean, target)
    assert len(pairs) == 2
    kinds = {(p.korean is not None, p.target is not None) for p in pairs}
    assert (True, False) in kinds
    assert (False, True) in kinds
