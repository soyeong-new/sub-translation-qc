"""한국어 원문에서 같은/비슷한 대사(회상 장면, 반복 대사)를 묶는다 — 언어와 무관하고 LLM을 쓰지 않는다."""
import re
from difflib import SequenceMatcher
from typing import List

MIN_LEN = 6  # 이보다 짧은 줄("응", "네, 알겠어")은 상황마다 번역이 달라지는 게 정상이라 제외
THRESHOLD = 0.85
_LEN_GAP = 6  # 글자 수 차이가 이만큼 넘으면 비슷한 대사로 보지 않는다(유사도 계산 생략)


def normalize_korean(text: str) -> str:
    text = re.sub(r"[（(][^）)]*[）)]|\[[^\]]*\]|<[^>]+>", "", text)  # 화자 표시·효과음·태그
    return re.sub(r"[^\w가-힣]", "", text)


def find_repeated_groups(items: List[dict], min_len: int = MIN_LEN, threshold: float = THRESHOLD) -> List[List[str]]:
    """items: [{"id", "text"}]. 같거나 비슷한(유사도 ≥ threshold) 줄의 id 묶음을 입력 순서대로 돌려준다(2개 이상인 묶음만)."""
    norm = [(it["id"], normalize_korean(it["text"])) for it in items]
    norm = [(i, n) for i, n in norm if len(n) >= min_len]
    parent = list(range(len(norm)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(norm)):
        for b in range(a + 1, len(norm)):
            na, nb = norm[a][1], norm[b][1]
            if na == nb or (abs(len(na) - len(nb)) <= _LEN_GAP and _similar(na, nb, threshold)):
                parent[find(b)] = find(a)

    groups: dict = {}
    for idx, (item_id, _) in enumerate(norm):
        groups.setdefault(find(idx), []).append(item_id)
    return [g for g in groups.values() if len(g) > 1]


def _similar(a: str, b: str, threshold: float) -> bool:
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= threshold and m.quick_ratio() >= threshold and m.ratio() >= threshold


def match_references(current: List[dict], references: List[dict], min_len: int = MIN_LEN,
                     threshold: float = THRESHOLD) -> dict:
    """current 줄마다 references(앞 회차 줄, 최근 회차가 앞에 오도록 정렬)에서 같거나 가장 비슷한 줄을 찾아
    {current_id: reference_id}로 돌려준다. 정확히 같은 줄이 우선이고, 비슷한 줄끼리는 유사도가 높은 쪽,
    같으면 앞쪽(최근 회차)이 이긴다. 짧은 줄과 짝이 없는 줄은 결과에 없다."""
    refs = [(r["id"], normalize_korean(r["text"])) for r in references]
    refs = [(i, n) for i, n in refs if len(n) >= min_len]
    exact: dict = {}
    for i, n in refs:
        exact.setdefault(n, i)
    result = {}
    for c in current:
        n = normalize_korean(c["text"])
        if len(n) < min_len:
            continue
        if n in exact:
            result[c["id"]] = exact[n]
            continue
        best, best_ratio = None, threshold
        # ponytail: 전수 비교 — 앞 회차가 수십 개로 늘어 느려지면 길이 구간으로 색인한다
        for i, rn in refs:
            if abs(len(n) - len(rn)) > _LEN_GAP:
                continue
            m = SequenceMatcher(None, n, rn)
            if m.real_quick_ratio() < best_ratio or m.quick_ratio() < best_ratio:
                continue
            ratio = m.ratio()
            if ratio > best_ratio or (best is None and ratio >= threshold):
                best, best_ratio = i, ratio
        if best is not None:
            result[c["id"]] = best
    return result
