from app.knowledge.loader import load_knowledge, load_sensitive_terms


def test_load_knowledge_combines_honorifics_and_idioms():
    text = load_knowledge()
    assert "호칭" in text or "형" in text
    assert len(text) > 0


def test_load_sensitive_terms_returns_flat_list():
    terms = load_sensitive_terms()
    assert isinstance(terms, list)
    assert len(terms) > 0
