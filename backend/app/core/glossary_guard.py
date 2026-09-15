"""등록된 고유명사 표준 표기(canonical)가 검수 문구에 실제로 쓰였는지
diff로 확인하고, 확신할 수 있을 때만 맞추거나 새 표기를 뽑아낸다.

LLM 프롬프트 지시만으로는 강제되지 않는다(실제로 뚫린 사례가 있었음) —
그래서 여기는 순수 Python 문자열 비교만 쓴다. original_text와 candidate
사이에서 바뀐 부분(diff의 replace 지점) 중, 양쪽 다 대문자로 시작하는
짧은 단어 묶음만 "고유명사 후보"로 본다. 후보가 0개거나 여럿이면(애매하면)
추측하지 않고 그대로 둔다 — 남은 위험은 export 시점 경고(safety net)로
넘긴다."""

import re
from difflib import SequenceMatcher

_TOKEN_RE = re.compile(r"\S+|\s+")
_LEADING_PUNCT_RE = re.compile(r"^[¡¿\"'(\[-]*")
_TRAILING_PUNCT_RE = re.compile(r"[.,!?;:\"')\]]*$")
_MAX_SPAN_WORDS = 3


def _split_preserve_ws(text: str) -> list:
    return _TOKEN_RE.findall(text)


def _core(token: str) -> str:
    return _TRAILING_PUNCT_RE.sub("", _LEADING_PUNCT_RE.sub("", token))


def _is_name_word(token: str) -> bool:
    core = _core(token)
    return bool(core) and core[0].isupper()


def _find_name_span(original_text: str, other_text: str) -> str | None:
    """other_text 안에서 고유명사 후보 하나만 확신할 수 있으면 그 표기(원문
    그대로, 구두점 포함)를 반환한다. 문장 맨 앞 단어는 그냥 문장 시작이라
    대문자일 수 있어 후보에서 제외한다."""
    orig_parts = _split_preserve_ws(original_text)
    other_parts = _split_preserve_ws(other_text)
    orig_word_idx = [i for i, p in enumerate(orig_parts) if not p.isspace()]
    other_word_idx = [i for i, p in enumerate(other_parts) if not p.isspace()]
    orig_words = [orig_parts[i] for i in orig_word_idx]
    other_words = [other_parts[i] for i in other_word_idx]

    matcher = SequenceMatcher(None, orig_words, other_words)
    candidates = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or i1 == 0 or j1 == 0:
            continue
        if (i2 - i1) > _MAX_SPAN_WORDS or (j2 - j1) > _MAX_SPAN_WORDS:
            continue
        if not all(_is_name_word(w) for w in orig_words[i1:i2]):
            continue
        if not all(_is_name_word(w) for w in other_words[j1:j2]):
            continue
        candidates.append((other_word_idx[j1], other_word_idx[j2 - 1]))

    if len(candidates) != 1:
        return None
    start, end = candidates[0]
    return "".join(other_parts[start:end + 1])


def patch_missing_canonical(korean_text: str, original_text: str,
                             candidate_text: str, glossary_entries: list) -> str:
    """korean_text에 등록 용어가 있는데 candidate_text에 표준 표기가 그대로
    없으면, diff로 찾은 위치를 표준 표기로 바꾼다. 후보가 애매하면 그대로
    둔다(추측 안 함)."""
    patched = candidate_text
    for entry in glossary_entries:
        canonical, korean_term = entry.get("canonical"), entry.get("korean_term")
        if not canonical or not korean_term or korean_term not in korean_text:
            continue
        if canonical in patched:
            continue
        matched = _find_name_span(original_text, patched)
        if matched is None:
            continue
        patched = patched.replace(
            matched, _LEADING_PUNCT_RE.match(matched).group(0) + canonical
            + _TRAILING_PUNCT_RE.search(matched).group(0), 1)
    return patched


def find_canonical_overrides(korean_text: str, original_text: str,
                              final_text: str, glossary_entries: list) -> list:
    """검수자가 직접 입력한 final_text가 표준 표기와 다르면(의도적으로 다른
    표기를 선택했다고 보고) (entry_id, 새_canonical) 쌍을 돌려준다 — 애매한
    diff 결과는 건드리지 않는다."""
    overrides = []
    for entry in glossary_entries:
        canonical, korean_term = entry.get("canonical"), entry.get("korean_term")
        entry_id = entry.get("entry_id")
        if not entry_id or not korean_term or korean_term not in korean_text:
            continue
        if canonical and canonical in final_text:
            continue
        matched = _find_name_span(original_text, final_text)
        if matched is None:
            continue
        new_canonical = _core(matched)
        if new_canonical and new_canonical != canonical:
            overrides.append((entry_id, new_canonical))
    return overrides


def _trailing_name_run(words: list, j1: int, j2: int):
    start = j2
    while start > j1 and _is_name_word(words[start - 1]):
        start -= 1
    return (start, j2) if start < j2 else (None, None)


def _leading_name_run(words: list, j1: int, j2: int):
    end = j1
    while end < j2 and _is_name_word(words[end]):
        end += 1
    return (j1, end) if end > j1 else (None, None)


def _find_canonical_replacement(original_text: str, other_text: str, canonical: str) -> str | None:
    """other_text 안에서, original_text 속 canonical이 있던 자리가 diff상 어디로
    이동했는지 추적해 그 자리의 표기(구두점 포함, other_text 원문 그대로)를
    반환한다. canonical의 위치를 원문에서 하나로 확신할 수 없거나(0번/2번
    이상 등장), 이동한 자리를 하나로 확신할 수 없으면 None.

    patch_missing_canonical/_find_name_span과 달리 canonical이 이미 original_text
    어딘가에 정확히 존재한다는 것을 앵커로 요구한다 — "미래"처럼 대문자로
    시작하는 흔한 단어가 이름으로 오인되는 걸 막기 위함(canonical이 원문에
    없으면 애초에 탐색 자체를 하지 않는다)."""
    orig_parts = _split_preserve_ws(original_text)
    other_parts = _split_preserve_ws(other_text)
    orig_word_idx = [i for i, p in enumerate(orig_parts) if not p.isspace()]
    other_word_idx = [i for i, p in enumerate(other_parts) if not p.isspace()]
    orig_words = [orig_parts[i] for i in orig_word_idx]
    other_words = [other_parts[i] for i in other_word_idx]

    canon_words = canonical.split()
    n = len(canon_words)
    canon_range = None
    for i in range(len(orig_words) - n + 1):
        if all(_core(orig_words[i + k]) == canon_words[k] for k in range(n)):
            if canon_range is not None:
                return None  # 원문에 canonical이 두 번 이상 등장 -> 애매
            canon_range = (i, i + n - 1)
    if canon_range is None:
        return None  # 원문에 canonical이 아예 없음 -> 앵커 없음, 손대지 않음

    matcher = SequenceMatcher(None, orig_words, other_words)
    j_start, j_end = None, None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if i2 <= canon_range[0] or i1 > canon_range[1]:
            continue
        if tag == "equal":
            ov_i1, ov_i2 = max(i1, canon_range[0]), min(i2, canon_range[1] + 1)
            seg = (j1 + (ov_i1 - i1), j1 + (ov_i2 - i1))
        elif i1 == canon_range[0] and i2 - 1 == canon_range[1]:
            seg = (j1, j2)  # 이 블록 전체가 정확히 canonical 자리와 일치
        elif i2 - 1 == canon_range[1]:
            seg = _trailing_name_run(other_words, j1, j2)  # canonical이 블록 끝쪽에 걸침
        elif i1 == canon_range[0]:
            seg = _leading_name_run(other_words, j1, j2)  # canonical이 블록 앞쪽에 걸침
        else:
            return None  # canonical이 블록 중간에 걸침 -> 애매, 포기
        if seg[0] is None:
            return None
        j_start = seg[0] if j_start is None else min(j_start, seg[0])
        j_end = seg[1] if j_end is None else max(j_end, seg[1])

    if j_start is None or j_end is None or j_end <= j_start:
        return None
    if (j_end - j_start) > _MAX_SPAN_WORDS:
        return None
    start, end = other_word_idx[j_start], other_word_idx[j_end - 1]
    return "".join(other_parts[start:end + 1])


def revert_canonical_regression(korean_text: str, original_text: str,
                                 candidate_text: str, glossary_entries: list) -> str:
    """이미 원문(original_text)에 등록 표기가 정확히 쓰여 있었는데,
    candidate_text에서 LLM이 그걸 다른(미등록) 표기로 바꿔버린 경우 되돌린다.
    patch_missing_canonical은 원문에 표기가 없어도(근접한 오탈자 교정 등)
    동작하지만, 이 함수는 원문에 canonical이 정확히 있었다는 걸 앵커로
    요구해 더 안전하다 — 그 대신 원문에 canonical이 없는 케이스는 못 잡는다."""
    patched = candidate_text
    for entry in glossary_entries:
        canonical, korean_term = entry.get("canonical"), entry.get("korean_term")
        if not canonical or not korean_term or korean_term not in korean_text:
            continue
        if canonical in patched:
            continue
        matched = _find_canonical_replacement(original_text, patched, canonical)
        if matched is None:
            continue
        patched = patched.replace(
            matched, _LEADING_PUNCT_RE.match(matched).group(0) + canonical
            + _TRAILING_PUNCT_RE.search(matched).group(0), 1)
    return patched


if __name__ == "__main__":
    entries = [{"entry_id": "e1", "korean_term": "강오크", "canonical": "Kang-ok"}]
    assert patch_missing_canonical(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Gang-ok.", entries) == "Oye, Kang-ok."
    assert patch_missing_canonical(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang-ok.", entries) == "Oye, Kang-ok."
    assert find_canonical_overrides(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang Orc!", entries) == [("e1", "Orc")]
    assert find_canonical_overrides(
        "야, 강오크.", "Oye, Kang Hulk.", "Oye, Kang-ok.", entries) == []

    regression_entries = [
        {"entry_id": "e1", "korean_term": "강오크", "canonical": "Kang Hulk"},
        {"entry_id": "e2", "korean_term": "강옥", "canonical": "Gang-ok"},
    ]
    assert revert_canonical_regression(
        "너 말이야, 너, 강오크!", "Estoy hablando contigo. Kang Hulk.",
        "Te estoy hablando a ti. ¡Kang Ok!", regression_entries
    ) == "Te estoy hablando a ti. ¡Kang Hulk!"  # 문장 전체가 다시 쓰여도 이름 자리는 추적
    assert revert_canonical_regression(
        "야, 강오크!", "Oye, Kang Hulk.", "Oye, Kang Ok.", regression_entries
    ) == "Oye, Kang Hulk."  # 다단어 canonical의 일부만 diff돼도 중복 없이 복원
    mirae_entries = [{"entry_id": "e3", "korean_term": "미래", "canonical": "Mirae"}]
    assert revert_canonical_regression(
        "미래에 우리는 결혼을 할거야", "En el futuro, nos vamos a casar.",
        "En el futuro, nos casaremos.", mirae_entries
    ) == "En el futuro, nos casaremos."  # "미래" 동음이의어(사람 이름 아님) 오탐 방지
    assert revert_canonical_regression(
        "강오크가 강오크를 불렀다", "Kang Hulk called Kang Hulk.",
        "Kang Hulk called Kang Ok.", regression_entries
    ) == "Kang Hulk called Kang Ok."  # 원문에 canonical이 2번 등장 -> 애매해서 포기
    assert revert_canonical_regression(
        "야, 강오크!", "Oye, Kang Hulk.", "Oye, Kang Hulk!", regression_entries
    ) == "Oye, Kang Hulk!"  # 이미 정상 표기면 아무것도 안 바뀜
    assert revert_canonical_regression(
        "다른 문장입니다", "Oye, Kang Hulk.", "Oye, Kang Ok.", regression_entries
    ) == "Oye, Kang Ok."  # korean_term이 korean_text에 없으면 손대지 않음
    print("ok")
