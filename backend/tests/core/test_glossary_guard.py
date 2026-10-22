from app.core.glossary_guard import patch_missing_canonical, find_canonical_overrides

_ENTRIES = [{"entry_id": "e1", "korean_term": "강오크", "category": "person",
             "canonical": "Kang-ok"}]


def test_patch_missing_canonical_fixes_real_bug_case():
    """회귀(사용자 보고): "강오크"가 등록돼 있는데도 AI가 다시 번역하면서
    표준 표기가 아닌 다른 철자("Gang-ok")를 냈다 — diff로 바뀐 위치를 찾아
    표준 표기로 강제한다."""
    patched = patch_missing_canonical(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Gang-ok.", _ENTRIES)
    assert patched == "Oye, Kang-ok."


def test_patch_missing_canonical_noop_when_already_correct():
    patched = patch_missing_canonical(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang-ok.", _ENTRIES)
    assert patched == "Oye, Kang-ok."


def test_patch_missing_canonical_skips_when_korean_term_absent():
    patched = patch_missing_canonical(
        "다른 문장", "Oye, Kang Hulk.", "Oye, Gang-ok.", _ENTRIES)
    assert patched == "Oye, Gang-ok."


def test_patch_missing_canonical_leaves_ambiguous_diff_untouched():
    """바뀐 후보가 여럿(둘 다 대문자 단어)이면 추측하지 않고 그대로 둔다."""
    patched = patch_missing_canonical(
        "야, 강오크.", "Oye, Kang Hulk, y Juan.", "Oye, Gang-ok, y Pedro.", _ENTRIES)
    assert patched == "Oye, Gang-ok, y Pedro."


def test_find_canonical_overrides_detects_reviewer_intentional_spelling():
    """검수자가 직접 다른 표기(Orc)로 고쳤으면, 강제로 되돌리지 않고 그
    표기를 새 표준 표기 후보로 뽑아낸다(자가 치유)."""
    overrides = find_canonical_overrides(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang Orc!", _ENTRIES)
    assert overrides == [("e1", "Orc")]


def test_find_canonical_overrides_empty_when_matches_canonical():
    overrides = find_canonical_overrides(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang-ok.", _ENTRIES)
    assert overrides == []


def test_term_in_respects_number_boundaries_but_not_for_names():
    from app.core.glossary_guard import term_in
    assert term_in("10억", "10억을 줘")
    assert not term_in("10억", "110억 짜리")
    assert not term_in("100억", "1,100억")
    assert not term_in("100억", "1.100억")
    assert term_in("100억", "네,100억")            # 쉼표 앞이 숫자가 아니면 일치
    assert not term_in("200", "2000원") and not term_in("200", "200.5원")
    assert term_in("200", "일주일에 200 맞아요")
    assert term_in("김현", "야,김현이 왔어")         # 이름은 구두점 뒤에서도 일치
    assert not term_in("김현", "")


def test_patch_and_overrides_do_not_fire_on_longer_number_containing_the_term():
    entries = [{"korean_term": "10억", "canonical": "mil millones de wones", "entry_id": "e1"}]
    assert patch_missing_canonical("110억 짜리", "Cuesta Mil", "Cuesta Mil", entries) == "Cuesta Mil"
    assert find_canonical_overrides("110억 짜리", "x", "y", entries) == []
