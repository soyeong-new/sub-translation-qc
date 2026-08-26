import pytest
from app.core.grammar_necessity import (
    check_grammar_necessity, resolve_gender_in_texts, resolve_gender_groups_in_texts,
    _detect_korean_formality, _detect_korean_gender, _has_any_gender_hint,
)

PROFILE = {"language": "es", "variant": "LATAM"}


def test_flags_gender_for_gendered_predicate_adjective():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Estoy muy cansada hoy."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["formality_check_needed"] is True
    assert result[0]["candidate_words"] == ["cansada"]


def test_flags_gender_for_passive_voice_participle_referring_to_people():
    """회귀: 수동태/완료형 분사("Han sido invitados")는 spaCy가 ADJ가 아니라
    VERB+VerbForm=Part로 태깅해서, ADJ만 보던 예전 필터는 이걸 놓쳤다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Han sido invitados todos."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["candidate_words"] == ["invitados"]


def test_does_not_flag_gender_for_html_tag_artifact_in_target_text():
    """회귀: 자막 원문의 오프스크린/독백 표시용 <i>...</i> 태그를 안 지우고
    spaCy에 넣으면 "<"가 별도 토큰으로 떨어지고 남은 "i>...내용...</i"
    파편이 형용사로 오태깅되며 성별 후보로 잘못 잡히는 사례가 실제 리뷰
    화면에서 발견됐다(design §2026-08 성별판정 정확도 개선)."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "<i>¿Soo-jung?</i> Está ocupada."}], PROFILE)
    assert result[0]["candidate_words"] == ["ocupada"]


def test_does_not_flag_gender_for_dash_prefixed_dialogue_marker_artifact():
    """회귀: 대사 앞의 화자 구분용 대시가 다음 단어에 붙어("-No", "-Lim
    Ho-young") spaCy가 통째로 하나의 (잘못된) 형용사로 오태깅하는 사례가
    실제 데이터에서 반복 관찰됐다(design §2026-08 성별판정 정확도 개선)."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "-No, no es así.\n-¿Qué?"}], PROFILE)
    assert result[0]["gender_check_needed"] is False
    assert result[0]["candidate_words"] == []


def test_does_not_flag_gender_for_invariant_adjective():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "El cielo es azul."}], PROFILE)
    assert result[0]["gender_check_needed"] is False


def test_flags_amod_adjective_modifying_person_noun_outside_old_whitelist():
    """회귀(실제 오작동, 이전엔 놓쳤던 케이스): "profesor"(교수)처럼 사람을
    가리키는 명사는 흔하지만, 이전 구현은 24개짜리 하드코딩 화이트리스트에
    없는 사람 명사(doctor/profesor 등)를 전부 놓쳤다. 이제 spaCy는 순수
    형태소(Gender 자질 유무)만 보고, "실제로 사람 얘기인지"는 LLM이
    판단하므로 이 케이스가 후보로 잡혀야 한다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "El profesor cansado se fue."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["candidate_words"] == ["cansado"]


def test_flags_amod_adjective_modifying_inanimate_noun_as_candidate():
    """정책 변경(이전엔 오탐 방지 화이트리스트로 여기서 걸렀지만, 이제 그
    판단은 LLM의 몫이다): "tiempo compartido"(타임셰어)의 "compartido"도
    Gender 형태소가 있는 ADJ이므로 spaCy 단계에서는 후보로 잡힌다 — 이게
    실제로 사람 얘기가 아니라는 판단은 이 함수의 책임이 아니다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Pero me dio varias membresías de tiempo compartido."}],
        PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["candidate_words"] == ["compartido"]


def test_flags_formality_even_when_usted_pronoun_is_dropped():
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


def test_flags_gender_for_portuguese_predicate_adjective():
    """다국어 확장: LANGUAGE_TO_SPACY_MODEL에 pt가 추가된 뒤에도 스페인어와
    동일하게 spaCy 범용 Gender 형태소 기반으로 성별 후보를 잡아야 한다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Ela é bonita."}],
        {"language": "pt", "variant": "BR"})
    assert result[0]["gender_check_needed"] is True
    assert result[0]["candidate_words"] == ["bonita"]


def test_does_not_flag_gender_for_english_adjective_without_gender_morph():
    """영어 형용사는 spaCy에 성별 형태소가 없어(cansado/cansada 같은 게
    없음) 성별 후보로 안 잡혀야 한다 — 별도 예외처리 없이 범용 로직만으로
    자연스럽게 그렇게 된다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "She became beautiful."}],
        {"language": "en", "variant": "US"})
    assert result[0]["gender_check_needed"] is False


def test_does_not_flag_formality_for_english_even_with_finite_verb():
    """회귀: 영어는 tú/usted 같은 문법적 존댓말/반말 구분이 없다. profile에
    formality_applicable: false가 없으면, "동사가 활용형으로 끝나는가"만
    보는 기존 판단 로직이 거의 모든 영어 문장에 걸려 줄마다 격식 확인을
    요구하는 잘못된 화면이 된다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Could you help me?"}],
        {"language": "en", "variant": "US", "formality_applicable": False})
    assert result[0]["formality_check_needed"] is False


def test_flags_formality_by_default_when_profile_omits_formality_applicable():
    """formality_applicable 필드가 아예 없는 기존 프로파일(es/pt)은 계속
    기본값 True로 동작해야 한다 — 하위호환 회귀 방지."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puede venir aquí?"}], PROFILE)
    assert result[0]["formality_check_needed"] is True


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


def test_defaults_formality_to_informal_when_korean_ending_does_not_match_honorific_pattern():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?", "korean_text": "음"}], PROFILE)
    assert result[0]["resolved_formality"] == "informal"


def test_formality_stays_unresolved_when_korean_text_is_empty():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?", "korean_text": ""}], PROFILE)
    assert result[0]["resolved_formality"] is None


def test_formality_policy_uses_last_sentence_ending_when_mixed():
    assert _detect_korean_formality("감사합니다. 알겠습니다.") == "formal"
    assert _detect_korean_formality("감사합니다. 너 진짜 가?") == "informal"


@pytest.mark.parametrize("text", [
    "감사합니다", "갈까요", "이거 예쁘죠", "이따가 갈게요", "오십시오",
    "알겠습니다", "괜찮아요", "괜찮습니까", "하십시오",
])
def test_detects_formal_ending_regardless_of_stem_final_consonant(text):
    assert _detect_korean_formality(text) == "formal"


@pytest.mark.parametrize("text", ["그거 알아?", "음", "정말 좋아", "이거 봐", "고마워", "괜찮아", "가자"])
def test_defaults_to_informal_without_formal_particle(text):
    assert _detect_korean_formality(text) == "informal"


def test_distinguishes_nikka_question_ending_from_because_connective():
    assert _detect_korean_formality("그러니까") == "informal"
    assert _detect_korean_formality("추우니까 옷 입어") == "informal"
    assert _detect_korean_formality("괜찮습니까?") == "formal"
    assert _detect_korean_formality("이거 드시겠습니까") == "formal"


@pytest.mark.parametrize("text", [
    "그거 뭐야?", "그런데 왜 그래?", "그리고 나서 갔어", "그냥 그렇대",
    "형태가 이상해", "이모티콘 보내줘",
])
def test_does_not_false_positive_on_words_containing_pronoun_or_kinship_prefix(text):
    assert _detect_korean_gender(text) is None


def test_detects_third_person_female_pronoun_when_grammatically_isolated():
    assert _detect_korean_gender("그녀가 말했다") == "female"
    assert _detect_korean_gender("그녀석 뭐야") is None


def test_does_not_infer_male_from_bare_third_person_pronoun():
    assert _detect_korean_gender("그가 말했다") is None


def test_resolves_gender_from_korean_kinship_term_when_single_candidate():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado.",
          "korean_text": "오빠 언제부터 계신 거예요?"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] == "male"


def test_gender_stays_unresolved_when_korean_terms_conflict():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado.",
          "korean_text": "오빠랑 언니 같이 왔어?"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] is None


@pytest.mark.parametrize("text", [
    "저 선생님 사랑하고 있어요", "주사가 심하네", "아유, 씨 아유, 아유, 씨",
    "아, 난 또 되게 각별한 사이인 줄 알았네요 식사는요?",
])
def test_has_no_gender_hint_when_no_referring_word_at_all(text):
    """회귀: 실제 오판 사례들(design §2026-08 성별판정 정확도 개선) — 문장에
    화자/청자 자신을 가리키는 단어가 전혀 없고 대명사·활용형·감탄사뿐이면
    강한/약한 단서 둘 다 없는 게 맞다."""
    assert _has_any_gender_hint(text) is False


@pytest.mark.parametrize("text", [
    "아, 이 새끼는 왜 잔칫날 상복을 입고 왔어? 미친 거야?",  # 약한 단서(새끼)
    "그 인간 죽었니?",  # 약한 단서(인간)
    "나 임신했어",  # 강한 단서(여성 단어 목록)
    "이 놈 봐라",  # 약한 단서, NNB로 태깅되는 예외 케이스
])
def test_has_gender_hint_when_weak_or_strong_referring_word_present(text):
    assert _has_any_gender_hint(text) is True


def test_resolves_mixed_group_to_masculine_plural_when_candidate_is_plural():
    """아빠+엄마처럼 남녀 지칭어가 둘 다 나와도, 후보가 그 집단을 가리키는
    복수형이면 상충이 아니라 스페인어 문법상 남성복수가 기본형이다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Vivimos juntos muchos años.",
          "korean_text": "아빠랑 엄마랑 같이 산 세월이 있잖아"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] == "male"


def test_mixed_terms_stay_unresolved_when_candidate_is_singular():
    """복수형이 아니면(한 사람만 가리킴) 남녀 지칭어 동시 등장은 여전히
    상충으로 취급해 사람 확인으로 넘긴다 — 위 규칙은 집단 지칭에만 쓴다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado.",
          "korean_text": "아빠랑 엄마랑 같이 왔어?"}], PROFILE)
    assert result[0]["resolved_gender_from_korean"] is None


def test_korean_gender_bypass_skipped_when_multiple_candidates_present():
    """회귀(설계 의도): 후보 단어가 둘 이상이면 한국어 단서 하나로 어느
    쪽을 가리키는지 안전하게 구분할 수 없으므로, 아무리 한국어 원문에
    명확한 호칭이 있어도 자동 확정하지 않고 LLM/사람 판단으로 넘긴다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Él está cansado y ella está enojada.",
          "korean_text": "오빠 왜 그래?"}], PROFILE)
    assert result[0]["candidate_words"] == ["cansado", "enojada"]
    assert result[0]["resolved_gender_from_korean"] is None


def test_candidate_words_preserve_sentence_order_for_multiple_candidates():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Un hombre cansado y una mujer contenta caminan."}],
        PROFILE)
    assert result[0]["candidate_words"] == ["cansado", "contenta"]
    assert result[0]["candidate_word_lemmas"] == ["cansado", "contento"]


def test_resolve_gender_in_texts_flips_mismatched_ending():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy cansado.", "gender": "female"}], "es")
    assert result["p1"] == "Estoy cansada."


def test_resolve_gender_in_texts_leaves_already_matching_text_untouched():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy cansada.", "gender": "female"}], "es")
    assert result["p1"] == "Estoy cansada."


def test_resolve_gender_in_texts_leaves_invariant_adjective_untouched():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy muy feliz.", "gender": "male"}], "es")
    assert result["p1"] == "Estoy muy feliz."


def test_resolve_gender_in_texts_collapses_slash_notation_to_matching_gender():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Te ves cansado/a.", "gender": "male"}], "es")
    assert result["p1"] == "Te ves cansado."


def test_resolve_gender_in_texts_collapses_slash_notation_to_opposite_gender():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Te ves cansado/a.", "gender": "female"}], "es")
    assert result["p1"] == "Te ves cansada."


def test_resolve_gender_groups_in_texts_applies_by_candidate_index_order():
    """새 설계: 그룹은 lemma가 아니라 "문장 속 후보 등장 순서"(candidate_
    indices)로 매칭된다 — "Él está cansado y ella está enojado."에서
    후보는 등장 순서대로 [cansado(0), enojado(1)]이다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Él está cansado y ella está enojado.",
          "groups": [{"candidate_indices": [0], "gender": "female"},
                     {"candidate_indices": [1], "gender": "male"}]}],
        "es",
    )
    assert result["p1"] == "Él está cansada y ella está enojado."


def test_resolve_gender_groups_in_texts_ignores_unresolved_group():
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Él está cansado y ella está enojado.",
          "groups": [{"candidate_indices": [0], "gender": None},
                     {"candidate_indices": [1], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Él está cansado y ella está enojada."


def test_resolve_gender_groups_in_texts_one_group_can_span_multiple_candidate_indices():
    """같은 인물이 형용사를 두 개 쓰면("Está cansado y aburrido.") 한 그룹이
    후보 인덱스 [0, 1]을 모두 가리켜야 둘 다 같이 바뀐다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Está cansado y aburrido.",
          "groups": [{"candidate_indices": [0, 1], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Está cansada y aburrida."


def test_resolve_gender_groups_in_texts_does_not_clobber_duplicate_surface_text():
    """회귀: 두 인물이 같은 표면형 단어를 쓰면("Juan está cansado, pero María
    no está cansado") 토큰 위치(tok.idx) 기반 치환이라 후보 인덱스로 정확히
    그 토큰만 바뀐다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Juan está cansado, pero María no está cansado.",
          "groups": [{"candidate_indices": [0], "gender": "male"},
                     {"candidate_indices": [1], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Juan está cansado, pero María no está cansada."


def test_resolve_gender_groups_in_texts_skips_indices_beyond_current_candidate_count():
    """이 텍스트를 다시 파싱했을 때 후보가 예전보다 줄었으면(예: 문장이
    바뀜) 존재하지 않는 인덱스는 조용히 무시한다 — 존재하는 후보에는
    영향 없다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Estoy cansado.",
          "groups": [{"candidate_indices": [0], "gender": "female"},
                     {"candidate_indices": [5], "gender": "male"}]}],
        "es",
    )
    assert result["p1"] == "Estoy cansada."
