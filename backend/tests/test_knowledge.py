from app.knowledge.loader import load_knowledge, load_sensitive_terms


def test_load_knowledge_combines_honorifics_and_idioms():
    text = load_knowledge()
    assert "호칭" in text or "형" in text
    assert len(text) > 0


def test_load_knowledge_has_no_hardcoded_spanish_examples():
    """회귀: knowledge/는 언어 구분 없이 모든 언어(영어/프랑스어/포르투갈어/
    스페인어) 프롬프트에 그대로 섞여 들어간다 — 예전엔 honorifics.yaml의
    bad/good 예문이 스페인어 문장으로 박혀 있어서, 영어를 교정시킬 때도
    "bad: Hermano, ¿comiste?" 같은 스페인어가 참고 지식으로 같이 들어갔다.
    이 파일에 담는 규칙은 특정 언어의 실제 문장 예시가 아니라 언어 무관
    개념 설명이어야 한다."""
    text = load_knowledge()
    assert "¿" not in text
    assert "Hermano" not in text


def test_load_knowledge_omits_bad_good_suffix_when_absent(tmp_path):
    import app.knowledge.loader as loader_module
    (tmp_path / "rule.yaml").write_text(
        "rules:\n  - term: 예시\n    rule: 예시 규칙\n", encoding="utf-8")
    text = loader_module.load_knowledge(str(tmp_path))
    assert text == "- 예시: 예시 규칙"


def test_load_sensitive_terms_returns_flat_list():
    terms = load_sensitive_terms()
    assert isinstance(terms, list)
    assert len(terms) > 0
