import pytest
from app.core.grammar_necessity import check_grammar_necessity

PROFILE = {"language": "es", "variant": "LATAM"}


def test_flags_gender_for_gendered_predicate_adjective():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Estoy muy cansada hoy."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["formality_check_needed"] is True


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


def test_resolves_formality_as_formal_from_korean_honorific_ending():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puede venir aquí?",
          "korean_text": "언제부터 계신 거예요?"}], PROFILE)
    assert result[0]["resolved_formality"] == "formal"


def test_resolves_formality_as_informal_from_korean_casual_ending():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?",
          "korean_text": "그거 알아?"}], PROFILE)
    assert result[0]["resolved_formality"] == "informal"


def test_formality_stays_unresolved_when_korean_ending_is_ambiguous():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?", "korean_text": "음"}], PROFILE)
    assert result[0]["resolved_formality"] is None


def test_resolves_gender_from_korean_kinship_term():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado.",
          "korean_text": "오빠 언제부터 계신 거예요?"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] == "male"


def test_gender_stays_unresolved_when_korean_terms_conflict():
    """호칭이 둘 다 나와 상충하면(예: 오빠와 언니가 같이 언급) 어느 쪽을
    가리키는지 알 수 없으므로 자동 판정하지 않는다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado.",
          "korean_text": "오빠랑 언니 같이 왔어?"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] is None


def test_grammatical_person_detects_first_second_third():
    first = check_grammar_necessity(
        [{"id": "p1", "target_text": "Estoy cansado."}], PROFILE)
    second = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Estás cansado?"}], PROFILE)
    third = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado."}], PROFILE)
    assert first[0]["grammatical_person"] == "1"
    assert second[0]["grammatical_person"] == "2"
    assert third[0]["grammatical_person"] == "3"
