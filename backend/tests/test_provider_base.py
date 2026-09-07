from app.providers.base import (
    build_glossary_block,
    build_batch_scope_intro,
    build_requery_scope_intro,
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
