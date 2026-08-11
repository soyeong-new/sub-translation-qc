import pytest
from app.core.grammar_necessity import (
    check_grammar_necessity, resolve_gender_in_texts, resolve_gender_groups_in_texts,
    _detect_korean_formality, _detect_korean_gender,
)

PROFILE = {"language": "es", "variant": "LATAM"}


def test_flags_gender_for_gendered_predicate_adjective():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Estoy muy cansada hoy."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["formality_check_needed"] is True
    # 검수자가 스테퍼에서 "정확히 어느 단어 때문에 성별을 고르는지" 볼 수
    # 있어야 한다 — 성별 표시가 걸린 실제 단어를 반환해야 함.
    assert result[0]["gender_words"] == ["cansada"]


def test_flags_gender_for_passive_voice_participle_referring_to_people():
    """회귀: 수동태/완료형 분사("Han sido invitados")는 spaCy가 ADJ가 아니라
    VERB+VerbForm=Part로 태깅해서, ADJ만 보던 예전 필터는 이걸 놓쳤다. 사람을
    가리키는 분사도 형용사와 똑같이 성별 확인이 필요하다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Han sido invitados todos."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["gender_words"] == ["invitados"]


def test_does_not_flag_gender_for_invariant_adjective():
    """"azul"(불변 형용사)은 성별에 따라 형태가 바뀌지 않으므로 성별 확인이
    필요 없다 — spaCy도 이런 단어엔 Gender 형태소 자질을 붙이지 않는다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "El cielo es azul."}], PROFILE)
    assert result[0]["gender_check_needed"] is False


def test_does_not_flag_gender_for_adjective_modifying_inanimate_noun():
    """회귀(사용자 재현, 실제 오작동): "tiempo compartido"(타임셰어)의
    "compartido"는 amod로 "tiempo"(시간, 사물)를 수식할 뿐 사람과 무관한데,
    예전엔 이것도 성별 확인 대상으로 잡혔다. 검수자가 이 질문에 "여성"으로
    답하면(질문 자체가 뭘 묻는지 이해하기 어려우니 오답 가능성이 높음)
    "compartido"가 "compartida"로 조용히 잘못 바뀌어 최종 자막에 그대로
    나가는 사고가 실제로 있었다 — amod는 수식받는 명사가 사람일 근거가
    있을 때만(이름/대명사/흔한 사람 명사) 성별 확인 대상으로 삼는다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Pero me dio varias membresías de tiempo compartido."}],
        PROFILE)
    assert result[0]["gender_check_needed"] is False
    assert result[0]["gender_groups"] == []


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


def test_defaults_formality_to_informal_when_korean_ending_does_not_match_honorific_pattern():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?", "korean_text": "음"}], PROFILE)
    assert result[0]["resolved_formality"] == "informal"


def test_formality_stays_unresolved_when_korean_text_is_empty():
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "¿Puedes venir?", "korean_text": ""}], PROFILE)
    assert result[0]["resolved_formality"] is None


def test_formality_policy_uses_last_sentence_ending_when_mixed():
    """정책(버그 아님): 한 자막 줄에 문체가 다른 절/문장이 여러 개 섞이면
    마지막 종결어미로 줄 전체의 문체를 대표한다. 뒤에 오는 어미가 그 줄이
    최종적으로 남기는 인상에 가깝다는 실용적 판단 — 완벽한 문체 분석이
    목표가 아니다."""
    assert _detect_korean_formality("감사합니다. 알겠습니다.") == "formal"
    assert _detect_korean_formality("감사합니다. 너 진짜 가?") == "informal"


@pytest.mark.parametrize("text", [
    "감사합니다",       # 하다 동사 + ㅂ니다(모음 어간 축약형) — 이전엔 "습니다"만 잡아 누락
    "갈까요",           # ㄹ까요(받침 축약형) — 이전엔 "을까요"만 잡아 누락
    "이거 예쁘죠",       # 죠(지요 축약) — 이전엔 목록에 아예 없었음
    "이따가 갈게요",     # 을게요/게요 — 이전엔 목록에 아예 없었음
    "오십시오",         # 하십시오체 명령형 — 이전엔 목록에 아예 없었음
    "알겠습니다",       # 기존에도 잡히던 케이스(회귀 확인용)
    "괜찮아요", "괜찮습니까", "하십시오",
])
def test_detects_formal_ending_regardless_of_stem_final_consonant(text):
    assert _detect_korean_formality(text) == "formal"


@pytest.mark.parametrize("text", ["그거 알아?", "음", "정말 좋아", "이거 봐", "고마워", "괜찮아", "가자"])
def test_defaults_to_informal_without_formal_particle(text):
    assert _detect_korean_formality(text) == "informal"


def test_distinguishes_nikka_question_ending_from_because_connective():
    """"니까"는 하십시오체 질문(괜찮습니까?)과 "because" 연결어미(그러니까)에
    둘 다 쓰인다. 이 파이프라인은 한국어를 문장 단위가 아니라 스페인어 자막
    타이밍 기준으로 토막 내므로, STT 조각이 "~니까"에서 끊긴 채로("그러니까")
    resolved_formality에 들어올 수 있다. 형태소 분석기가 어말어미(EF, 질문
    어미)와 연결어미(EC, because)를 태그로 이미 구분해주므로 물음표 유무나
    받침 계산 없이도 정확히 갈린다."""
    assert _detect_korean_formality("그러니까") == "informal"
    assert _detect_korean_formality("추우니까 옷 입어") == "informal"
    assert _detect_korean_formality("괜찮습니까?") == "formal"
    assert _detect_korean_formality("이거 드시겠습니까") == "formal"  # 물음표 없어도 formal


@pytest.mark.parametrize("text", [
    "그거 뭐야?",     # "그것"의 준말 — "그"가 대명사(NP)가 아니라 이 단어 자체의 일부
    "그런데 왜 그래?",  # 접속부사 한 단어, "그"가 따로 분리되지 않음
    "그리고 나서 갔어",
    "그냥 그렇대",
    "형태가 이상해",   # "형"이 "형태"라는 별개 명사의 일부일 뿐 호칭이 아님
    "이모티콘 보내줘",  # "이모"가 "이모티콘"의 일부일 뿐 호칭이 아님
])
def test_does_not_false_positive_on_words_containing_pronoun_or_kinship_prefix(text):
    """"그"는 한국어에서 가장 흔한 지시어/접속 표현의 접두사라(그거/그런데/
    그리고/그냥), substring 검사로는 3인칭 대명사 "그"와 구분이 안 된다(둘 다
    똑같이 "그"로 시작하고 띄어쓰기도 없다). "형"/"이모" 같은 짧은 호칭도
    "형태"/"이모티콘" 같은 무관한 단어의 일부로 끼어든다. 형태소 분석기가
    실제 품사(대명사 NP vs 그 외)로 태깅한 토큰만 인정해야 이 오탐이 없다."""
    assert _detect_korean_gender(text) is None


def test_detects_third_person_female_pronoun_when_grammatically_isolated():
    assert _detect_korean_gender("그녀가 말했다") == "female"
    assert _detect_korean_gender("그녀석 뭐야") is None  # "그"(관형사)+"녀석", 대명사 아님


def test_does_not_infer_male_from_bare_third_person_pronoun():
    """"그"(3인칭 남성 대명사)는 male 자동 확정 근거로 안 쓴다 — 품사가
    대명사(NP)로 정확히 태깅돼도, 그 하나만으로 사람 확인 없이 스페인어
    텍스트에 성별을 적용하기엔 근거가 약하다(resolved_gender_from_korean이
    non-None이면 검수 스텝을 건너뛰므로, 틀리면 사람이 볼 기회가 없다)."""
    assert _detect_korean_gender("그가 말했다") is None


@pytest.mark.parametrize("text", [
    "여자친구가 생겼대",     # "여친이 생긴 사람"의 성별이 필요한데 여친 본인 성별이 잡힘
    "아내분이 기다리세요",   # "아내가 있는 사람" 아니라 아내 본인 성별이 잡힘
])
def test_relationship_terms_can_misidentify_the_actual_referent(text):
    """알려진 한계(회귀 아님, 현재 동작을 명시적으로 기록): 여자친구/아내 같은
    관계어는 그 관계어 자신의 성별을 반환하지, 그 관계를 가진("그에게 여자친구가
    있다") 진짜 번역 대상 인물의 성별을 반환하는 게 아니다. 이 값도 non-None이면
    검수 없이 바로 적용되므로, 실제 번역 대상과 다른 사람일 수 있다 — 이
    테스트는 그 위험을 드러내 놓기 위한 것이지, "고쳐졌다"는 뜻이 아니다."""
    assert _detect_korean_gender(text) is not None


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


def test_grammatical_person_detects_person_from_pronoun_without_verb():
    """회귀(실제 운영에서 발견): "Tú primero."처럼 동사 없는 생략문은
    성별 표시 단어(primero)의 인칭 정보를 대명사(Tú)가 직접 들고 있다 —
    동사만 보면 아무것도 못 찾아 성별 확인 화면에 "몇 인칭인지"가 아예
    안 뜨는 버그가 있었다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Sí. Tú primero."}], PROFILE)
    assert result[0]["gender_check_needed"] is True
    assert result[0]["grammatical_person"] == "2"


def test_grammatical_person_prefers_token_closest_to_gendered_word():
    """복문에서 인칭이 다른 절이 여러 개 섞여 있으면, 성별 표시 단어와
    가장 가까운(문맥상 실제로 그 단어가 딸린) 인칭을 골라야 한다 — 무조건
    첫 번째로 나온 동사의 인칭을 쓰면 엉뚱한 절의 인칭을 가져오게 된다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Lo hago yo. Tú vas primero."}], PROFILE)
    assert result[0]["grammatical_person"] == "2"


def test_resolve_gender_in_texts_flips_mismatched_ending():
    """회귀: AI에게 "성별을 반영해달라"고 부탁하는 대신, 파이썬이 결정론적
    문법 규칙으로 직접 어미를 바꿔야 한다."""
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy cansado.", "gender": "female"}], "es")
    assert result["p1"] == "Estoy cansada."


def test_resolve_gender_in_texts_leaves_already_matching_text_untouched():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy cansada.", "gender": "female"}], "es")
    assert result["p1"] == "Estoy cansada."


def test_resolve_gender_in_texts_leaves_invariant_adjective_untouched():
    """"feliz"(성별 무관 형용사)는 -o/-a로 안 끝나 규칙에 안 걸리므로
    그대로 둔다 — 잘못 건드리지 않는 게 안전한 쪽이다."""
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Estoy muy feliz.", "gender": "male"}], "es")
    assert result["p1"] == "Estoy muy feliz."


def test_resolve_gender_in_texts_collapses_slash_notation_to_matching_gender():
    """회귀: "cansado/a"처럼 AI가 성별 미확정 슬래시 표기를 남겼을 때, 슬래시
    앞 단어가 이미 목표 성별과 같아도(그래서 어미 자체는 안 바뀌어도) 슬래시
    + 접미사는 지워져야 한다."""
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Te ves cansado/a.", "gender": "male"}], "es")
    assert result["p1"] == "Te ves cansado."


def test_resolve_gender_in_texts_collapses_slash_notation_to_opposite_gender():
    result = resolve_gender_in_texts(
        [{"id": "p1", "text": "Te ves cansado/a.", "gender": "female"}], "es")
    assert result["p1"] == "Te ves cansada."


def test_gender_groups_splits_words_by_distinct_referent():
    """회귀(사용자 피드백 "인칭을 제대로 구분 못하는 경우가 있다"): 한 줄에
    성별이 다른 인물이 둘이면(각자 자기 주어를 가짐) gender_groups가 인물별로
    따로 묶여야 한다 — 성별 하나를 문장 전체에 뭉뚱그려 적용하면 안 되므로."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Él está cansado y ella está enojada."}], PROFILE)
    groups = result[0]["gender_groups"]
    assert [g["words"] for g in groups] == [["cansado"], ["enojada"]]


def test_gender_groups_carry_referent_anchor_word_for_reviewer_display():
    """다인물 줄에서 "인물 1"/"인물 2"라는 번호만으로는 검수자가 이게
    누구 얘기인지 문장을 직접 읽고 유추해야 한다 — _referent_key가 이미
    찾아낸 앵커 단어(이름/대명사)를 그룹에 실어서 화면에 보여준다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Juan está cansado y María está enojada."}], PROFILE)
    groups = result[0]["gender_groups"]
    assert [g["referent"] for g in groups] == ["Juan", "María"]


def test_gender_groups_keeps_same_referent_words_in_one_group():
    """"Está cansado y aburrido."는 생략된 주어 하나를 공유하는 술어 두
    개다(등위접속, 새 주어 없음) — 같은 인물이므로 그룹이 하나여야 한다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Está cansado y aburrido."}], PROFILE)
    groups = result[0]["gender_groups"]
    assert len(groups) == 1
    assert groups[0]["words"] == ["cansado", "aburrido"]


def test_gender_groups_splits_by_modified_noun_for_attributive_adjectives():
    """명사를 직접 수식하는 형용사(amod)도 그 명사가 다르면 다른 인물이다
    — "hombre cansado"와 "mujer feliz"는 서로 다른 사람 얘기다."""
    result = check_grammar_necessity(
        [{"id": "p1", "target_text": "Un hombre cansado y una mujer contenta caminan."}], PROFILE)
    groups = result[0]["gender_groups"]
    assert [g["words"] for g in groups] == [["cansado"], ["contenta"]]


def test_resolve_gender_groups_in_texts_applies_each_group_only_to_its_own_words():
    """다인물 문장에서 그룹별로 확정된 성별이 그 그룹의 단어에만 적용되고
    다른 그룹의 단어는 절대 건드리지 않아야 한다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Él está cansado y ella está enojado.",
          "groups": [{"lemmas": ["cansado"], "gender": "female"},
                     {"lemmas": ["enojado"], "gender": "male"}]}],
        "es",
    )
    assert result["p1"] == "Él está cansada y ella está enojado."


def test_resolve_gender_groups_in_texts_ignores_unresolved_group():
    """아직 확정 안 된(gender=None) 그룹은 건드리지 않는다 — 확정된 그룹만
    적용한다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Él está cansado y ella está enojado.",
          "groups": [{"lemmas": ["cansado"], "gender": None},
                     {"lemmas": ["enojado"], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Él está cansado y ella está enojada."


def test_resolve_gender_groups_in_texts_survives_lemma_collision_across_referents():
    """회귀: spaCy는 cansado/cansada를 같은 lemma("cansado")로 정규화한다.
    두 사람이 같은 형용사를 성별만 다르게 쓰면("Juan está cansado y María
    está cansada") 예전엔 lemma_to_gender 딕셔너리에서 두 그룹이 같은 키로
    충돌해 뒤 그룹이 앞 그룹의 확정 성별을 덮어썼다 — Juan의 이미 맞는 단어가
    엉뚱하게 cansada로 바뀌는 사고가 실제로 났었다. 그룹 인덱스로 매칭하면
    이 충돌이 없다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Juan está cansado y María está cansada.",
          "groups": [{"lemmas": ["cansado"], "gender": "male"},
                     {"lemmas": ["cansado"], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Juan está cansado y María está cansada."


def test_resolve_gender_groups_in_texts_does_not_clobber_duplicate_surface_text():
    """회귀: 두 인물이 같은 표면형 단어를 쓰면("Juan está cansado, pero María
    no está cansado") text.replace()는 내용으로 찾아 바꾸므로 한쪽만 고치려
    해도 문장에 있는 모든 "cansado"가 바뀌었다. 토큰 위치(tok.idx) 기반
    치환은 정확히 그 토큰만 바꾼다."""
    result = resolve_gender_groups_in_texts(
        [{"id": "p1", "text": "Juan está cansado, pero María no está cansado.",
          "groups": [{"lemmas": ["cansado"], "gender": "male"},
                     {"lemmas": ["cansado"], "gender": "female"}]}],
        "es",
    )
    assert result["p1"] == "Juan está cansado, pero María no está cansada."
