import pytest
from app.core.grammar_necessity import check_grammar_necessity

PROFILE = {"language": "es", "variant": "LATAM"}


def test_flags_gender_for_gendered_predicate_adjective():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Estoy muy cansada hoy."}], PROFILE)
    assert result == [
        {"id": "p1", "gender_check_needed": True, "formality_check_needed": True}
    ]


def test_does_not_flag_gender_for_invariant_adjective():
    """"azul"(불변 형용사)은 성별에 따라 형태가 바뀌지 않으므로 성별 확인이
    필요 없다 — spaCy도 이런 단어엔 Gender 형태소 자질을 붙이지 않는다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "El cielo es azul."}], PROFILE)
    assert result[0]["gender_check_needed"] is False


def test_flags_formality_even_when_usted_pronoun_is_dropped():
    """usted는 스페인어 문법상 3인칭 동사 활용을 그대로 쓰고, 자막에서는
    대명사 자체가 생략되는 경우가 흔하다 — 인칭만으로는 tú/usted를 구분할
    근거가 없으므로, 활용된 동사가 있으면 인칭과 무관하게 flag한다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puede venir aquí un momento?"}], PROFILE)
    assert result[0]["formality_check_needed"] is True


def test_flags_formality_for_tu_conjugated_line():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir aquí?"}], PROFILE)
    assert result[0]["formality_check_needed"] is True


def test_does_not_flag_formality_when_no_finite_verb_present():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¡Hola!"}], PROFILE)
    assert result[0]["formality_check_needed"] is False


def test_preserves_id_and_order_across_multiple_pairs():
    pairs = [
        {"id": "a", "target_text": "Estoy cansado."},
        {"id": "b", "target_text": "¡Hola!"},
        {"id": "c", "target_text": "¿Puede venir?"},
    ]
    result = check_grammar_necessity(pairs, PROFILE)
    assert [r["id"] for r in result] == ["a", "b", "c"]


def test_raises_for_unsupported_language():
    with pytest.raises(ValueError):
        check_grammar_necessity(
            [{"id": "p1", "target_text": "hello"}], {"language": "fr"})
