from app.core.pretreatment import run_pretreatment
from app.schemas import AlignedPair, SegmentText

GLOSSARY = [{"canonical": "Chulsoo", "aliases": ["Cholsu", "Chulsu"]}]
CTA_PATTERNS = [r"구독.{0,5}좋아요"]
PROFANITY = [{"term": "mierda", "replacement": "[삐-]"}]
SENSITIVE_TERMS = ["mierda", "pendejo"]


def _pair(text: str) -> AlignedPair:
    return AlignedPair(id="p1", target=SegmentText(start=0.0, end=1.0, text=text))


def test_glossary_replaces_alias_with_canonical_name():
    result = run_pretreatment([_pair("Cholsu가 왔다")], GLOSSARY, [], [], [], "tv1")
    assert result.pairs[0].target.text == "Chulsoo가 왔다"
    assert result.findings[0].category == "glossary"
    assert result.findings[0].status == "approved"
    assert result.findings[0].model == "사전필터"


def test_cta_pattern_is_removed():
    result = run_pretreatment([_pair("구독 좋아요 눌러주세요")], [], CTA_PATTERNS, [], [], "tv1")
    assert "구독" not in result.pairs[0].target.text
    assert result.findings[0].category == "cta"


def test_profanity_dictionary_entry_is_replaced():
    result = run_pretreatment([_pair("qué mierda")], [], [], PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.pairs[0].target.text == "qué [삐-]"
    assert result.findings[0].category == "sensitivity"
    assert result.findings[0].model == "사전필터"


def test_sensitive_term_not_in_profanity_dict_becomes_pending_hit():
    result = run_pretreatment([_pair("eres un pendejo")], [], [], PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.pending_sensitive_hits == [{"segment_id": "p1", "term": "pendejo"}]
    assert result.findings == []


def test_unmatched_text_is_unchanged_and_produces_no_findings():
    result = run_pretreatment([_pair("hola mundo")], GLOSSARY, CTA_PATTERNS, PROFANITY,
                               SENSITIVE_TERMS, "tv1")
    assert result.pairs[0].target.text == "hola mundo"
    assert result.findings == []
    assert result.pending_sensitive_hits == []


def test_pair_without_target_is_skipped():
    pair = AlignedPair(id="p1", target=None)
    result = run_pretreatment([pair], GLOSSARY, CTA_PATTERNS, PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.findings == []
    assert result.pending_sensitive_hits == []
