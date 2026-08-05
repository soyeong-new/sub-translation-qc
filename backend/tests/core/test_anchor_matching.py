from app.schemas import AlignedPair, SegmentText
from app.core.anchor_matching import find_anchor_candidates, find_relationship_anchor_candidates


def _pair(id_, text):
    return AlignedPair(id=id_, korean=SegmentText(start=0.0, end=1.0, text=text), target=None)


def test_find_relationship_anchor_candidates_matches_when_both_names_appear():
    scene = [_pair("p1", "민지야, 서준이가 그러는데")]
    relationships = [
        {"id": "r1", "speaker_label": "민지", "addressee_label": "서준"},
        {"id": "r2", "speaker_label": "민지", "addressee_label": "지훈"},
    ]
    result = find_relationship_anchor_candidates(scene, relationships)
    assert result == [{"id": "r1", "label": "민지 → 서준"}]


def test_find_relationship_anchor_candidates_requires_both_names_in_scene():
    scene = [_pair("p1", "민지야 밥 먹었어?")]
    relationships = [{"id": "r1", "speaker_label": "민지", "addressee_label": "서준"}]
    assert find_relationship_anchor_candidates(scene, relationships) == []


def test_find_relationship_anchor_candidates_returns_empty_for_empty_roster():
    scene = [_pair("p1", "민지야 서준아")]
    assert find_relationship_anchor_candidates(scene, []) == []


def test_find_anchor_candidates_matches_label_appearing_in_scene_text():
    scene = [_pair("p1", "민지야, 밥 먹었어?")]
    roster = [{"id": "c1", "label": "민지"}, {"id": "c2", "label": "서준"}]
    result = find_anchor_candidates(scene, roster)
    assert result == [{"id": "c1", "label": "민지"}]


def test_find_anchor_candidates_returns_empty_when_no_name_appears():
    scene = [_pair("p1", "저녁 뭐 먹을까?")]
    roster = [{"id": "c1", "label": "민지"}]
    assert find_anchor_candidates(scene, roster) == []


def test_find_anchor_candidates_checks_every_segment_in_scene_not_just_first():
    scene = [_pair("p1", "저녁 뭐 먹을까?"), _pair("p2", "서준아 너도 올래?")]
    roster = [{"id": "c1", "label": "민지"}, {"id": "c2", "label": "서준"}]
    result = find_anchor_candidates(scene, roster)
    assert result == [{"id": "c2", "label": "서준"}]


def test_find_anchor_candidates_matches_multiple_characters_in_one_scene():
    scene = [_pair("p1", "민지야 서준이 왔어")]
    roster = [{"id": "c1", "label": "민지"}, {"id": "c2", "label": "서준"},
              {"id": "c3", "label": "지훈"}]
    result = find_anchor_candidates(scene, roster)
    assert {c["id"] for c in result} == {"c1", "c2"}


def test_find_anchor_candidates_uses_korean_text_when_target_text_absent():
    """한국어 STT 원문에서 이름을 찾는다 — 로스터 라벨은 한국어 이름이므로
    대상언어(스페인어) 번역문이 아니라 korean_text를 대조 대상으로 삼는다."""
    scene = [AlignedPair(id="p1", korean=SegmentText(start=0.0, end=1.0, text="민지야!"),
                         target=SegmentText(start=0.0, end=1.0, text="¡Minji!"))]
    roster = [{"id": "c1", "label": "민지"}]
    result = find_anchor_candidates(scene, roster)
    assert result == [{"id": "c1", "label": "민지"}]
