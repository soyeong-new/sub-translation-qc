from app.core.repeated_lines import find_repeated_groups, normalize_korean


def _items(*texts):
    return [{"id": str(i), "text": t} for i, t in enumerate(texts)]


def test_normalize_strips_speaker_labels_and_punctuation():
    assert normalize_korean("（여학생1）F4다!") == normalize_korean("(여학생) F4다")
    assert normalize_korean("<i>아빠는  팔 안 아파.</i>") == "아빠는팔안아파"


def test_exact_repeat_grouped_even_with_different_speaker_label():
    groups = find_repeated_groups(_items("（일봉）아빠는 팔 안 아파", "다른 대사입니다 정말로", "아빠는 팔 안 아파!"))
    assert groups == [["0", "2"]]


def test_near_repeat_grouped():
    groups = find_repeated_groups(_items(
        "그저 이런 명품은 '옷이 상전이다' 생각하면 되는 거야",
        "그저 이런 명품은 '옷이 상전이다' 생각하면 되는 거예요",
    ))
    assert groups == [["0", "1"]]


def test_short_lines_ignored():
    assert find_repeated_groups(_items("네, 알겠어", "네, 알겠어")) == []


def test_unrelated_lines_not_grouped():
    assert find_repeated_groups(_items("오늘은 날씨가 정말 좋네요", "내일은 비가 올 거라고 하네요")) == []


def test_chain_of_repeats_is_one_group():
    groups = find_repeated_groups(_items("자폭하라 자폭하라 자폭하라", "자폭하라! 자폭하라! 자폭하라!", "자폭하라 자폭하라 자폭하라"))
    assert groups == [["0", "1", "2"]]


def test_match_references_prefers_exact_then_best_fuzzy_and_earlier_reference():
    from app.core.repeated_lines import match_references
    cur = [{"id": "c1", "text": "아빠는 팔 안 아파"}, {"id": "c2", "text": "그저 앉으나 서나 조심조심"},
           {"id": "c3", "text": "전혀 관계없는 대사입니다 정말"}, {"id": "c4", "text": "네"}]
    refs = [{"id": "r1", "text": "아빠는 팔 안 아파!"}, {"id": "r2", "text": "전혀 다른 줄이에요 진짜로"},
            {"id": "r3", "text": "앉으나 서나 조심조심"}, {"id": "r4", "text": "네"}]
    assert match_references(cur, refs) == {"c1": "r1", "c2": "r3"}  # 문장부호만 다르면 같은 줄, 짧은 줄·짝 없는 줄은 제외
    assert match_references(cur[:1], [refs[0], refs[0] | {"id": "r9"}]) == {"c1": "r1"}  # 같으면 앞(최근 회차)
