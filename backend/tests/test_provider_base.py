from app.providers.base import (
    build_glossary_block,
    build_batch_scope_intro,
    build_requery_scope_intro,
    build_verification_checklist,
    BATCH_SKIP_CLEAN_LINE,
)


def test_build_glossary_block_returns_empty_string_for_no_entries():
    assert build_glossary_block([]) == ""


def test_build_glossary_block_formats_entries_with_aliases():
    entries = [
        {"korean_term": "김현", "category": "person", "canonical": "Kim Hyun",
         "aliases": ["짱구"]},
        {"korean_term": "설악산", "category": "place", "canonical": "Mount Seorak",
         "aliases": []},
    ]
    block = build_glossary_block(entries)
    assert block.startswith("[작품 용어집]\n")
    assert "- 김현 (person): Kim Hyun (별칭: 짱구)" in block
    assert "- 설악산 (place): Mount Seorak" in block
    assert "설악산 (place): Mount Seorak (별칭:" not in block
    assert block.endswith("\n\n")


def test_build_batch_scope_intro_default_is_five_step():
    intro = build_batch_scope_intro()
    assert "[5단계 체크리스트]" in intro
    assert "5개 카테고리를 억지로" in intro


def test_build_batch_scope_intro_with_glossary_is_six_step():
    intro = build_batch_scope_intro("[작품 용어집]\n- 김현 (person): Kim Hyun\n\n")
    assert "[6단계 체크리스트]" in intro
    assert "6개 카테고리를 억지로" in intro


def test_build_requery_scope_intro_default_is_five_step():
    intro = build_requery_scope_intro()
    assert "[5단계 체크리스트]" in intro


def test_build_requery_scope_intro_with_glossary_is_six_step():
    intro = build_requery_scope_intro("[작품 용어집]\n- 김현 (person): Kim Hyun\n\n")
    assert "[6단계 체크리스트]" in intro


def test_build_verification_checklist_default_has_five_steps_no_glossary_item():
    checklist = build_verification_checklist("스페인어", BATCH_SKIP_CLEAN_LINE)
    assert "[5단계 순차 검증 체크리스트]" in checklist
    assert 'category: "glossary"' not in checklist
    assert "위 1~4번 문제를 고치기 위해" in checklist


def test_build_verification_checklist_with_glossary_block_has_six_steps():
    glossary_block = "[작품 용어집]\n- 김현 (person): Kim Hyun\n\n"
    checklist = build_verification_checklist("스페인어", BATCH_SKIP_CLEAN_LINE, glossary_block)
    assert "[6단계 순차 검증 체크리스트]" in checklist
    assert '5. 고유명사 표기 일관성 (category: "glossary"):' in checklist
    assert "아래 [작품 용어집]에 등록된 한국어 용어" in checklist
    assert "6. 이미 반영된 성별/격식 형태 보존:" in checklist
    assert "위 1~5번 문제를 고치기 위해" in checklist
