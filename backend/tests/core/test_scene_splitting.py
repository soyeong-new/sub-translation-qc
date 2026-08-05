from app.schemas import AlignedPair, SegmentText
from app.core.scene_splitting import split_into_scenes


def _pair(id_, start, end, text="hola"):
    return AlignedPair(id=id_, korean=None, target=SegmentText(start=start, end=end, text=text))


def test_split_into_scenes_groups_close_segments_together():
    pairs = [_pair("p1", 0.0, 2.0), _pair("p2", 3.0, 5.0), _pair("p3", 5.5, 7.0)]
    scenes = split_into_scenes(pairs, gap_threshold=5.0)
    assert len(scenes) == 1
    assert [p.id for p in scenes[0]] == ["p1", "p2", "p3"]


def test_split_into_scenes_splits_on_gap_over_threshold():
    pairs = [_pair("p1", 0.0, 2.0), _pair("p2", 10.0, 12.0)]
    scenes = split_into_scenes(pairs, gap_threshold=5.0)
    assert len(scenes) == 2
    assert [p.id for p in scenes[0]] == ["p1"]
    assert [p.id for p in scenes[1]] == ["p2"]


def test_split_into_scenes_sorts_unordered_input_by_start_time():
    """align()이 반환하는 pairs는 완전히 시간순이 아니다(정렬 안 된 target-only
    pair가 뒤에 붙음) — split_into_scenes가 직접 정렬해야 한다."""
    pairs = [_pair("p2", 10.0, 12.0), _pair("p1", 0.0, 2.0)]
    scenes = split_into_scenes(pairs, gap_threshold=5.0)
    assert len(scenes) == 2
    assert scenes[0][0].id == "p1"
    assert scenes[1][0].id == "p2"


def test_split_into_scenes_handles_korean_only_pair_with_no_target():
    pairs = [
        AlignedPair(id="p1", korean=SegmentText(start=0.0, end=2.0, text="안녕"), target=None),
        _pair("p2", 3.0, 5.0),
    ]
    scenes = split_into_scenes(pairs, gap_threshold=5.0)
    assert len(scenes) == 1


def test_split_into_scenes_returns_empty_list_for_no_pairs():
    assert split_into_scenes([], gap_threshold=5.0) == []
