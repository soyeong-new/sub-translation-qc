from app.providers.base import (
    build_glossary_block,
    build_batch_scope_intro,
    build_requery_scope_intro,
    build_verification_checklist,
    build_findings_schema_instruction,
    BATCH_SKIP_CLEAN_LINE,
    CATEGORY_ENUM,
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
    assert "오직 위 1~4번 문제만 고쳐라" in checklist


def test_build_verification_checklist_with_glossary_block_has_six_steps():
    glossary_block = "[작품 용어집]\n- 김현 (person): Kim Hyun\n\n"
    checklist = build_verification_checklist("스페인어", BATCH_SKIP_CLEAN_LINE, glossary_block)
    assert "[6단계 순차 검증 체크리스트]" in checklist
    assert '5. 고유명사 표기 일관성 (category: "glossary"):' in checklist
    assert "아래 [작품 용어집]에 등록된 한국어 용어" in checklist
    assert "6. 확정된 성별/격식 확인:" in checklist
    assert "오직 위 1~5번 문제만 고쳐라" in checklist


def test_category_enum_includes_glossary():
    assert "glossary" in CATEGORY_ENUM


def test_build_findings_schema_instruction_includes_glossary_category():
    instruction = build_findings_schema_instruction("lead in text")
    assert '"glossary"(작품 용어집에 등록된 고유명사 표기와 다르게 번역된 경우)' in instruction


def test_register_step_asks_to_report_mismatch_with_minimal_fix_and_names_no_language():
    checklist = build_verification_checklist("대상언어", BATCH_SKIP_CLEAN_LINE)
    step = checklist.split("5. 확정된 성별/격식 확인:")[1]
    assert "gender_groups" in step and "formality" in step
    assert "다른 형태" in step and "그 단어" in step and "문장 전체를 다시 쓰지" in step
    assert "되돌리지 마라" in step
    for name in ("스페인어", "포르투갈어", "프랑스어", "영어", "tú", "usted", "você"):
        assert name not in step
