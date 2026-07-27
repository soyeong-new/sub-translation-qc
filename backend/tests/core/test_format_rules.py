from app.schemas import AlignedPair, SegmentText
from app.core.format_rules import check_line_length, check_ellipsis, fix_ellipsis


def _pair(pid, text):
    return AlignedPair(id=pid, target=SegmentText(start=0, end=1, text=text))


def test_check_line_length_flags_long_single_line():
    long_line = "a" * 51
    violations = check_line_length([_pair("p1", long_line)])
    assert len(violations) == 1
    assert violations[0].rule == "line_length"


def test_check_line_length_flags_more_than_two_lines():
    text = "línea uno\nlínea dos\nlínea tres"
    violations = check_line_length([_pair("p1", text)])
    assert len(violations) == 1


def test_check_line_length_allows_two_short_lines():
    text = "línea corta\notra línea corta"
    violations = check_line_length([_pair("p1", text)])
    assert violations == []


def test_fix_ellipsis_collapses_four_dots_to_three():
    fixed, changed = fix_ellipsis("Espera....")
    assert fixed == "Espera..."
    assert changed is True


def test_fix_ellipsis_leaves_three_dots_untouched():
    fixed, changed = fix_ellipsis("Espera...")
    assert fixed == "Espera..."
    assert changed is False


def test_check_ellipsis_reports_auto_fixed_violation():
    violations = check_ellipsis([_pair("p1", "Espera.....")])
    assert len(violations) == 1
    assert violations[0].auto_fixed is True
    assert violations[0].fixed_text == "Espera..."
