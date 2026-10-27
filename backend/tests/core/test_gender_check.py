import pytest

from app.core.gender_check import find_gender_mismatches, only_inflection_changed
from app.core.grammar_necessity import _resolve_model
from app.core.pipeline import _run_gender_mismatch_pass
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
