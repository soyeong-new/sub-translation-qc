from app.schemas import SegmentText
from app.core.pronoun_hints import find_pronoun_hint


def test_find_pronoun_hint_returns_none_when_no_overlapping_segment():
    english_segments = [SegmentText(start=10.0, end=12.0, text="She left.")]
    assert find_pronoun_hint(0.0, 2.0, english_segments) is None


def test_find_pronoun_hint_counts_he_and_she_case_insensitively():
    english_segments = [SegmentText(start=0.0, end=2.0, text="He said she was tired. HE left.")]
    result = find_pronoun_hint(0.0, 2.0, english_segments)
    assert result == {"text": "He said she was tired. HE left.", "he_count": 2, "she_count": 1}


def test_find_pronoun_hint_matches_word_boundaries_not_substrings():
    """"her"가 "herd"나 "there" 같은 단어의 일부로 오탐되면 안 된다."""
    english_segments = [SegmentText(start=0.0, end=2.0, text="There is a herd of sheep.")]
    result = find_pronoun_hint(0.0, 2.0, english_segments)
    assert result == {"text": "There is a herd of sheep.", "he_count": 0, "she_count": 0}


def test_find_pronoun_hint_picks_best_overlapping_segment():
    english_segments = [
        SegmentText(start=0.0, end=1.0, text="He arrived."),
        SegmentText(start=5.0, end=7.0, text="She left."),
    ]
    result = find_pronoun_hint(5.5, 6.5, english_segments)
    assert result == {"text": "She left.", "he_count": 0, "she_count": 1}


def test_find_pronoun_hint_returns_zero_counts_when_overlap_has_no_pronouns():
    english_segments = [SegmentText(start=0.0, end=2.0, text="Let's go now.")]
    result = find_pronoun_hint(0.0, 2.0, english_segments)
    assert result == {"text": "Let's go now.", "he_count": 0, "she_count": 0}
