from app.schemas import AlignedPair, SegmentText
from app.core.format_rules import check_line_length, check_ellipsis, fix_ellipsis, rewrap_line


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


def test_check_ellipsis_captures_pre_fix_text_as_original_text():
    """회귀 테스트(important): pipeline.py는 check_ellipsis를 두 체크포인트
    (최초, GPT 이후 최종 재체크)에서 호출한다. FormatViolation이 그 시점의
    "고치기 전" 텍스트를 스스로 담고 있지 않으면, 나중에 파이프라인 최종
    상태로부터 되짚어 재구성해야 하는데 그러면 두 체크포인트의 값이 뭉개진다.
    이 테스트는 check_ellipsis 자체가 검사 시점의 원문을 정확히 남기는지
    확인한다."""
    violations = check_ellipsis([_pair("p1", "Espera.....")])
    assert violations[0].original_text == "Espera....."


def test_check_line_length_captures_current_text_as_original_text():
    long_line = "a" * 51
    violations = check_line_length([_pair("p1", long_line)])
    assert violations[0].original_text == long_line


def test_rewrap_line_fits_content_that_only_needs_rebreaking():
    text = " ".join(["word"] * 20)  # 99자, 한 줄에 몰려있음 — 100자(50x2) 안에는 들어감
    result = rewrap_line(text)
    assert result is not None
    lines = result.split("\n")
    assert len(lines) <= 2
    assert all(len(ln) <= 50 for ln in lines)


def test_rewrap_line_returns_none_when_content_too_long_even_optimally_wrapped():
    text = " ".join(["word"] * 40)  # 199자 — 아무리 잘 나눠도 2줄x50자 안에 안 들어감
    assert rewrap_line(text) is None


def test_rewrap_line_returns_none_for_single_unbreakable_word():
    assert rewrap_line("가" * 60) is None


def test_rewrap_line_normalizes_existing_bad_linebreaks():
    text = "línea muy larga que\nno respeta el límite de caracteres por línea aquí"
    result = rewrap_line(text)
    assert result is not None
    assert all(len(ln) <= 50 for ln in result.split("\n"))


def test_rewrap_line_prefers_sentence_boundary_over_word_boundary():
    text = "안녕하세요 저는 홍길동입니다. 만나서 반갑습니다 잘 부탁드립니다"
    result = rewrap_line(text)
    assert result == "안녕하세요 저는 홍길동입니다.\n만나서 반갑습니다 잘 부탁드립니다"


def test_rewrap_line_does_not_split_inside_ellipsis():
    text = "그건 저도 잘 모르겠어요... 나중에 다시 물어봐 주시겠어요 부탁드립니다"
    result = rewrap_line(text)
    assert result is not None
    for ln in result.split("\n"):
        assert "..." not in ln or ln.count(".") in (0, 3)


def test_rewrap_line_falls_back_to_word_boundary_without_punctuation():
    text = " ".join(["word"] * 20)
    result = rewrap_line(text)
    assert "\n" in result
    assert "." not in result and "," not in result
