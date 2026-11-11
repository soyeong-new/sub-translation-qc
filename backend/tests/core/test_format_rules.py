import pytest
from app.schemas import AlignedPair, SegmentText
from app.core.format_rules import (
    check_line_length, check_ellipsis, fix_ellipsis, violates_line_length, normalize_speaker_dash, rewrap_line, check_reading_speed,
)


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


def test_check_reading_speed_flags_short_cue_with_long_text():
    """회귀(사용자 재현): 줄당 42자 제약은 통과해도, 1.2초짜리 짧은 큐에
    문장 두 개 분량이 들어가면 읽기 속도 기준으로는 위반이어야 한다."""
    pair = AlignedPair(id="p1", target=SegmentText(
        start=0.0, end=1.2, text="Ahora hasta se le caen los palillos pequeños."))
    violations = check_reading_speed([pair])
    assert len(violations) == 1
    assert violations[0].rule == "reading_speed"


def test_check_reading_speed_allows_short_text_in_short_cue():
    pair = AlignedPair(id="p1", target=SegmentText(start=0.0, end=1.2, text="Hola."))
    assert check_reading_speed([pair]) == []


def test_check_reading_speed_ignores_pairs_without_target():
    pair = AlignedPair(id="p1", korean=SegmentText(start=0.0, end=1.2, text="긴 한국어 문장" * 5))
    assert check_reading_speed([pair]) == []


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
    text = " ".join(["word"] * 16)  # 79자, 한 줄에 몰려있음 — 84자(42x2) 안에는 들어감
    result = rewrap_line(text)
    assert result is not None
    lines = result.split("\n")
    assert len(lines) <= 2
    assert all(len(ln) <= 42 for ln in lines)


def test_rewrap_line_returns_none_when_content_too_long_even_optimally_wrapped():
    text = " ".join(["word"] * 40)  # 179자 — 아무리 잘 나눠도 2줄x42자 안에 안 들어감
    assert rewrap_line(text) is None


def test_rewrap_line_returns_none_for_single_unbreakable_word():
    assert rewrap_line("가" * 60) is None


def test_rewrap_line_normalizes_existing_bad_linebreaks():
    text = "línea muy larga que\nno respeta el límite de caracteres por línea aquí"
    result = rewrap_line(text)
    assert result is not None
    assert all(len(ln) <= 42 for ln in result.split("\n"))


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
    text = " ".join(["word"] * 16)
    result = rewrap_line(text)
    assert "\n" in result
    assert "." not in result and "," not in result


def test_normalize_speaker_dash_adds_space_only_to_dash_lines():
    assert normalize_speaker_dash("-¿Eres el nuevo?\n-Sí.") == "- ¿Eres el nuevo?\n- Sí."
    assert normalize_speaker_dash("- Sí.\nHola Lim Ho-young") == "- Sí.\nHola Lim Ho-young"
    assert normalize_speaker_dash("--Espera") == "--Espera"


@pytest.mark.parametrize("text,expected", [
    ("Ya sabes… de todos modos te traje el plano para ti", "Ya sabes…\nde todos modos te traje el plano para ti"),       # 말줄임표
    ("Tengo una sola cosa que decirte: no vuelvas nunca a mi casa", "Tengo una sola cosa que decirte:\nno vuelvas nunca a mi casa"),  # 콜론
    ("Todo salió perfecto hoy; mañana veremos qué pasa con todo", "Todo salió perfecto hoy;\nmañana veremos qué pasa con todo"),     # 세미콜론
    ("- Claro que sí, te espero aquí mismo - Nos vemos mañana", "- Claro que sí, te espero aquí mismo\n- Nos vemos mañana"),       # 대사 전환
])
def test_rewrap_line_breaks_at_extended_punctuation(text, expected):
    assert rewrap_line(text) == expected


def test_rewrap_line_does_not_treat_double_dash_as_dialogue_switch():
    result = rewrap_line("Il a dit que -- enfin, tu sais bien -- il ne viendrait pas ce soir du tout")
    assert not any(line.startswith("--") for line in result.split("\n"))


_ES_RULES = {
    "conjunctions": ["y", "o", "pero", "porque"],
    "prepositions": ["de", "en", "con", "para"],
    "no_break_after": ["el", "la", "un", "una", "me", "no"],
}


def test_rewrap_line_prefers_preposition_over_plain_word_boundary():
    text = "Gané casi 10 millones de dólares en criptomonedas."
    assert rewrap_line(text, rules=_ES_RULES) == "Gané casi 10 millones\nde dólares en criptomonedas."


def test_rewrap_line_prefers_conjunction_before_plain_word_boundary():
    text = "pero nunca sabes si estaba en casa o en un motel."
    assert rewrap_line(text, rules=_ES_RULES) == "pero nunca sabes si estaba en casa\no en un motel."


def test_rewrap_line_avoids_breaking_after_article_when_alternative_exists():
    text = "Ella siempre quería comprar la casa grande del barrio viejo"
    first = rewrap_line(text, rules=_ES_RULES).split("\n")[0]
    assert not first.endswith(" la")


def test_rewrap_line_avoids_breaking_between_capitalized_names():
    text = "Todos dicen que Bool Gom Gang es el mejor equipo del mundo"
    result = rewrap_line(text, rules={})
    assert "Gom\nGang" not in result and "Bool\nGom" not in result


def test_rewrap_line_still_succeeds_when_every_candidate_is_forbidden():
    # 금지 위치만 남아도 줄바꿈 자체는 가능해야 한다(None → LLM 축약으로 가면 안 됨)
    text = " ".join(["no"] * 20)
    assert rewrap_line(text, rules=_ES_RULES) is not None


_FR_RULES = {"conjunctions": ["que", "parce"], "prepositions": ["de"], "no_break_after": [],
             "no_break_phrases": ["parce que", "est-ce que"]}


def test_rewrap_line_does_not_break_inside_no_break_phrase():
    text = "Je ne suis pas parti hier soir parce que personne ne m'avait prévenu"
    result = rewrap_line(text, rules=_FR_RULES)
    assert "parce\nque" not in result


def test_rewrap_line_keeps_space_before_french_punctuation_with_previous_word():
    text = "Tu es sûr de vouloir partir maintenant sans rien lui dire ?"
    result = rewrap_line(text, max_chars=40, rules={})
    assert not any(line.startswith("?") for line in result.split("\n"))


def test_rewrap_line_preserves_nbsp_and_never_splits_on_it():
    text = "Tu es sûr de vouloir partir maintenant sans rien lui dire ?"
    result = rewrap_line(text, max_chars=40, rules={})
    assert "dire ?" in result


def test_rewrap_line_keeps_lone_inverted_mark_with_next_word():
    text = "No puedo creer que hayas hecho eso otra vez ¡ Eres increíble de verdad!"
    result = rewrap_line(text, max_chars=40, rules={})
    assert not any(line.endswith("¡") for line in result.split("\n"))


def test_fix_ellipsis_turns_two_dots_into_three():
    assert fix_ellipsis("Espera..") == ("Espera...", True)
    assert fix_ellipsis("Espera... y.. ya") == ("Espera... y... ya", True)


_EN_RULES = {"prepositions": ["on", "in"], "no_break_after": ["your", "the"]}
_EN_43 = "if you do not have Shinhwa School\non your resume, don't even bother applying."


def test_line_up_to_tolerance_is_not_a_violation():
    assert not violates_line_length(_EN_43)          # 둘째 줄 43자
    assert violates_line_length("a" * 46)


def test_avoid_forbidden_gives_up_when_only_bad_breaks_fit():
    flat = _EN_43.replace("\n", " ")
    assert "your\nresume" in rewrap_line(flat, rules=_EN_RULES)   # 기존 동작: 어색해도 맞춘다
    assert rewrap_line(flat, rules=_EN_RULES, avoid_forbidden=True) is None
