from app.core.consistency_check import find_gender_conflicts, find_register_conflicts


def test_unconfirmed_character_with_gendered_lines_needs_confirmation():
    characters = [{"label": "인물1", "confirmed_gender": None, "gendered_segment_ids": ["p1", "p2"]}]
    conflicts = find_gender_conflicts(characters)
    assert len(conflicts) == 1
    assert conflicts[0]["label"] == "인물1"


def test_confirmed_character_is_skipped():
    characters = [{"label": "인물1", "confirmed_gender": "female", "gendered_segment_ids": ["p1"]}]
    assert find_gender_conflicts(characters) == []


def test_character_without_gendered_lines_is_skipped():
    characters = [{"label": "인물1", "confirmed_gender": None, "gendered_segment_ids": []}]
    assert find_gender_conflicts(characters) == []


def test_unconfirmed_relationship_needs_confirmation():
    rels = [{"speaker_label": "A", "addressee_label": "B",
             "confirmed_formality_level": None, "formality_segment_ids": ["p1"]}]
    conflicts = find_register_conflicts(rels)
    assert len(conflicts) == 1
