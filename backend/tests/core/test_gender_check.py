import pytest

from app.core.gender_check import find_gender_mismatches, only_inflection_changed
from app.core.grammar_necessity import _resolve_model
from app.core.pipeline import _run_gender_mismatch_pass
from app.language_profiles.loader import load_profile
from app.schemas import AlignedPair, Finding, SegmentText


def _pair(pid, text):
    return AlignedPair(id=pid, korean=SegmentText(start=0, end=1, text="한"),
                       target=SegmentText(start=0, end=1, text=text))


def _regs(pid, word, gender):
    return {pid: {"gender": None, "formality": None,
                  "gender_groups": [{"words": [word], "referent": "상대방", "gender": gender}]}}


@pytest.fixture(scope="module")
def nlp():
    return _resolve_model("es")


@pytest.mark.parametrize("text,word,gender", [
    ("Estás acalorado, ¿eh?", "acalorado", "female"),                       # 실제로 반영 안 됐던 3건
    ("- Qué loco. ¿Estás ebria?\n- Solo tomé una cerveza.", "ebria", "male"),
    ("Soy experto en ese campo.", "experto", "female"),
])
def test_finds_word_whose_gender_differs_from_confirmed(nlp, text, word, gender):
    found = find_gender_mismatches([_pair("p1", text)], _regs("p1", word, gender), nlp)
    assert found["p1"][0]["words"] == [word]


def test_ignores_adjective_that_agrees_with_a_noun_not_the_speaker(nlp):
    """"Es mi última oportunidad"의 última는 oportunidad(여성명사)에 맞춘 것이라 화자가 남성이어도 정상이다."""
    assert find_gender_mismatches(
        [_pair("p1", "¡No hay tiempo! Es mi última oportunidad.")], _regs("p1", "última", "male"), nlp) == {}


def test_ignores_matching_form_missing_groups_and_skipped_ids(nlp):
    assert find_gender_mismatches([_pair("p1", "Estás acalorada.")], _regs("p1", "acalorado", "female"), nlp) == {}
    assert find_gender_mismatches([_pair("p1", "Soy experto.")], {"p1": {"gender": "female"}}, nlp) == {}
    assert find_gender_mismatches(
        [_pair("p1", "Soy experto.")], _regs("p1", "experto", "female"), nlp, skip_ids={"p1"}) == {}


def test_inflection_check_accepts_ending_change_and_rejects_rewrites():
    assert only_inflection_changed("Soy experto en ese campo.", "Soy experta en ese campo.", ["experto"])
    assert only_inflection_changed("Estoy cansado y solo.", "Estoy cansada y sola.", ["cansado"])
    assert not only_inflection_changed("Soy experto en ese campo.", "Soy una experta en ese campo.", ["experto"])
    assert not only_inflection_changed("Soy experto en ese campo.", "Soy experta en este campo.", ["experto"])
    assert not only_inflection_changed("Soy experto.", "Soy experta!", ["experto"])        # 구두점이 바뀜
    assert not only_inflection_changed("Soy experto.", "Soy experto.", ["experto"])        # 안 바뀜


class _StubProvider:
    def __init__(self, corrected):
        self.corrected = corrected

    async def apply_gender_groups(self, items, profile):
        return [{"id": i["id"], "corrected_text": self.corrected[i["id"]]} for i in items]

    async def back_translate_with_claude(self, texts, profile):
        return [{"id": t["id"], "korean_text": "나는 그 분야 전문가야", "original_korean_text": "나는 전문가야"} for t in texts]


@pytest.mark.asyncio
async def test_pass_creates_pending_finding_with_korean_back_translation():
    pairs = [_pair("p1", "Soy experto en ese campo.")]
    warnings = []
    findings = await _run_gender_mismatch_pass(
        pairs, _StubProvider({"p1": "Soy experta en ese campo."}), {"language": "es"}, "tv",
        _regs("p1", "experto", "female"), [], warnings)
    assert len(findings) == 1
    f = findings[0]
    assert (f.segment_id, f.status, f.suggested_text) == ("p1", "pending", "Soy experta en ese campo.")
    assert "한국어 역번역 참고: 나는 그 분야 전문가야" in f.description
    assert "원본 한국어 역번역 참고" in f.description
    assert pairs[0].target.text == "Soy experto en ese campo."   # 문장은 적용하지 않는다


@pytest.mark.asyncio
async def test_pass_drops_llm_rewrites():
    rewritten = await _run_gender_mismatch_pass(
        [_pair("p1", "Soy experto en ese campo.")],
        _StubProvider({"p1": "Soy una gran experta en ese campo."}), {"language": "es"}, "tv",
        _regs("p1", "experto", "female"), [], [])
    assert rewritten == []


@pytest.mark.asyncio
async def test_pass_fixes_the_existing_card_instead_of_adding_a_second_one():
    """검증기가 오역 카드를 이미 올렸는데 그 제안문에 성별 오류가 남아 있으면, 새 카드 없이 그 카드의
    제안문 어미만 고치고 안내를 역번역 태그 앞에 끼워 넣는다."""
    pair = _pair("p1", "Yo ser experto en ese campo.")
    card = Finding(
        id="f1", target_version_id="tv", segment_id="p1", category="mistranslation",
        description="동사 오류. (한국어 역번역 참고: 나는 전문가야)",
        original_text=pair.target.text, suggested_text="Soy experto en ese campo.", confidence=1.0)
    created = await _run_gender_mismatch_pass(
        [pair], _StubProvider({"f1": "Soy experta en ese campo."}), {"language": "es"}, "tv",
        _regs("p1", "experto", "female"), [card], [])
    assert created == []                                          # 새 카드 없음
    assert card.suggested_text == "Soy experta en ese campo."
    assert card.description.index("성별 어미도") < card.description.index("한국어 역번역 참고")
    assert card.description.endswith("(한국어 역번역 참고: 나는 전문가야)")
    assert pair.target.text == "Yo ser experto en ese campo."     # 적용 전 카드면 번역문은 그대로


@pytest.mark.asyncio
async def test_pass_leaves_existing_card_alone_when_its_suggestion_has_the_right_gender():
    pair = _pair("p1", "Yo ser experto en ese campo.")
    card = Finding(
        id="f1", target_version_id="tv", segment_id="p1", category="mistranslation", description="d",
        original_text=pair.target.text, suggested_text="Soy experta en ese campo.", confidence=1.0)
    created = await _run_gender_mismatch_pass(
        [pair], _StubProvider({}), {"language": "es"}, "tv", _regs("p1", "experto", "female"), [card], [])
    assert created == [] and card.description == "d"


@pytest.mark.parametrize("lang,text,word", [
    ("fr", "La personne est respectée.", "respectée"),
    ("fr", "La victime est blessée.", "blessée"),
    ("es", "La persona es respetada.", "respetada"),
    ("es", "La víctima fue herida.", "herida"),
    ("pt", "A pessoa é respeitada.", "respeitada"),
    ("pt", "A vítima foi ferida.", "ferida"),
])
def test_ignores_predicate_that_agrees_with_subject_noun_not_the_person(lang, text, word):
    """서술어 자리의 분사는 head가 동사라도 주어 명사(성별 고정 명사 personne/persona/pessoa)에 맞춘 것이라
    사람이 남성이어도 어긋남이 아니다(사용자는 문법을 몰라 남성을 누른다)."""
    assert find_gender_mismatches([_pair("p1", text)], _regs("p1", word, "male"), _resolve_model(lang)) == {}


@pytest.mark.parametrize("lang,text,word", [
    ("fr", "Elle est fatiguée.", "fatiguée"),
    ("es", "Estoy cansada.", "cansada"),
    ("es", "Ella está cansada.", "cansada"),
    ("pt", "Ela está cansada.", "cansada"),
])
def test_still_finds_predicate_when_subject_is_not_a_noun(lang, text, word):
    """주어가 대명사이거나 생략이면 맞춘 명사가 없으니 기존처럼 어긋남으로 잡는다."""
    found = find_gender_mismatches([_pair("p1", text)], _regs("p1", word, "male"), _resolve_model(lang))
    assert found["p1"][0]["words"] == [word]


def _be(lang, variant):
    return {l.lower() for l in load_profile(lang, variant)["be_verb_lemmas"]}


@pytest.mark.parametrize("lang,variant,text,word,gender", [
    ("it", "IT", "Hai mai sentito parlare degli F4?!", "sentito", "female"),   # avere + 분사는 불변
    ("it", "IT", "Hai capito?", "capito", "female"),
    ("it", "IT", "Ma ce l'ha fatta per tre giorni.", "fatta", "male"),        # 관용구 farcela, la 고정
    ("it", "IT", "Non l'ho spinto io.", "spinto", "female"),
    ("fr", "FR", "Tu as mangé.", "mangé", "female"),
    ("es", "LATAM", "¿Has visto eso?", "visto", "female"),
    ("es", "LATAM", "Ya tengo decididos algunos elementos.", "decididos", "female"),  # tener + 분사는 목적어에 일치
])
def test_skips_participle_not_agreeing_with_person_when_profile_lists_be_verbs(lang, variant, text, word, gender):
    """have류 조동사 뒤 분사는 화자·청자의 성에 일치하지 않는다 — 목록이 없을 때만 잡히던 오탐."""
    nlp = _resolve_model(lang)
    assert find_gender_mismatches([_pair("p1", text)], _regs("p1", word, gender), nlp) != {}  # 목록 없으면 전부 비교(기존 동작)
    assert find_gender_mismatches(
        [_pair("p1", text)], _regs("p1", word, gender), nlp, be_lemmas=_be(lang, variant)) == {}


@pytest.mark.parametrize("lang,variant,text,word,gender", [
    ("it", "IT", "Sono stanco.", "stanco", "female"),
    ("it", "IT", "Sono andato a casa.", "andato", "female"),
    ("it", "IT", "Sono innamorata di lui.", "innamorata", "male"),
    ("it", "IT", "Tu sei stato bravo.", "bravo", "female"),       # spaCy가 sei/stato의 lemma를 못 읽어 야믈에 따로 넣음
    ("it", "IT", "Sei andato via.", "andato", "female"),
    ("fr", "FR", "Je suis fatigué.", "fatigué", "female"),
    ("fr", "FR", "Elle est allée au marché.", "allée", "male"),
    ("fr", "FR", "Tu es tombé hier.", "tombé", "female"),
    ("es", "LATAM", "Estoy cansado.", "cansado", "female"),
    ("es", "LATAM", "Fue invitado a la fiesta.", "invitado", "female"),
    ("pt", "BR", "Estou cansado.", "cansado", "female"),
    ("pt", "BR", "Ela foi convidado.", "convidado", "female"),
])
def test_still_finds_be_verb_structures_with_profile_be_verbs(lang, variant, text, word, gender):
    """be 동사 구조는 목록이 있어도 계속 잡아야 한다 — 언어별로 놓치는 게 없는지 확인."""
    found = find_gender_mismatches(
        [_pair("p1", text)], _regs("p1", word, gender), _resolve_model(lang), be_lemmas=_be(lang, variant))
    assert found["p1"][0]["words"] == [word]


@pytest.mark.parametrize("lang,variant", [("es", "LATAM"), ("pt", "BR"), ("fr", "FR"), ("it", "IT")])
def test_gendered_language_profiles_list_be_verbs(lang, variant):
    """목록이 비면 그 언어의 모든 구조를 비교하는 옛 동작으로 돌아간다 — 새 언어를 추가할 때 빠뜨리지 않게."""
    assert _be(lang, variant)


@pytest.mark.parametrize("lang,before,after,expected", [
    # 성별 고정 명사는 그대로인데 분사만 바뀜 → 되돌린다
    ("fr", "Je suis une personne très respectée.", "Je suis une personne très respecté.",
     "Je suis une personne très respectée."),
    ("es", "La persona es respetada.", "La persona es respetado.", "La persona es respetada."),
    ("pt", "Ele é a vítima ferida dessa história.", "Ele é a vítima ferido dessa história.",
     "Ele é a vítima ferida dessa história."),
    # 사람 성별을 따르는 명사는 명사도 같이 바뀐다 → 그대로 둔다
    ("es", "Mi amiga está cansada.", "Mi amigo está cansado.", "Mi amigo está cansado."),
    ("es", "Soy una enfermera dedicada.", "Soy un enfermero dedicado.", "Soy un enfermero dedicado."),
    # 일치 명사가 없는 단어는 그대로 둔다
    ("es", "Estoy cansada.", "Estoy cansado.", "Estoy cansado."),
    # 단어 수가 달라 짝을 못 맞추면 건드리지 않는다
    ("es", "Estoy cansada.", "Estoy muy cansado.", "Estoy muy cansado."),
])
def test_revert_orphan_changes(lang, before, after, expected):
    from app.core.gender_check import revert_orphan_changes
    assert revert_orphan_changes(_resolve_model(lang), before, after) == expected


def test_revert_orphan_changes_keeps_html_tags():
    from app.core.gender_check import revert_orphan_changes
    assert revert_orphan_changes(
        _resolve_model("fr"), "<i>La personne est respectée.</i>", "<i>La personne est respecté.</i>"
    ) == "<i>La personne est respectée.</i>"


def test_revert_orphan_changes_does_nothing_for_language_without_gender():
    """영어는 Gender 정보가 없어 빈 값끼리 '일치'로 보면 멀쩡한 변경이 되돌려진다."""
    from app.core.gender_check import revert_orphan_changes
    assert revert_orphan_changes(
        _resolve_model("en"), "He is a tall man.", "He is a short man.") == "He is a short man."
