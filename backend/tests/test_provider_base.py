from app.providers.base import build_glossary_block


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
