"""지적 카드에 띄울 "이웃 큐와 겹침" 경고를 계산하는 모듈.

한국어와 번역이 큐를 끊는 지점이 달라, AI가 "이 큐에 내용이 빠졌다"고 오판해 이웃 큐에
이미 번역된 내용을 제안문에 또 넣는다(승인하면 같은 대사가 두 번 나온다). 지적을 지우거나
승인을 막지는 않고 카드에 알림만 붙인다 — 단어 겹침만으로 지우면 정상 제안까지 버리게 된다(시험으로 확인).

규칙(로컬 DB 지적 8,500건에서 측정): 경고는 지적 약 5%, 알려진 중복 사례 5건 중 4건 포착.
- 긴 단어: 제안이 새로 추가한 단어 중 5글자 이상 단어가 이웃에도 있고, 그런 단어의 절반 이상일 때
- 짧은 대사: 추가분이 1~3단어이고 이웃 문장 하나와 통째로 같을 때(`Sí.`, `¡Rápido!`)

내용이 줄어드는 제안(대사 소실)은 단어 수로는 같은 뜻의 짧은 재작성과 구분되지 않아(실데이터 3건 모두
오탐) 경고하지 않는다."""

import difflib
import re
import unicodedata
from typing import Dict, List, Optional

MIN_LONG_WORD_CHARS = 5       # 겹침으로 세는 단어의 최소 글자 수 — 짧은 단어(de, que, el)는 어느 문장에나 있다
MIN_LONG_WORD_SHARE = 0.5     # 추가된 긴 단어 중 이웃과 겹쳐야 하는 비율
MAX_SHORT_ADDITION_WORDS = 3  # 이 단어 수 이하의 추가분은 "이웃 문장 통째 일치"로만 본다

_TAG_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?…\n]+")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def _words(text: str) -> List[str]:
    folded = unicodedata.normalize("NFD", _strip_tags(text).lower()).encode("ascii", "ignore").decode()
    return _WORD_RE.findall(folded)


def _added_words(original: str, suggested: str) -> List[str]:
    """제안이 새로 도입한 단어들 — 삽입·교체된 단어 중 원본에 없던 것만. 원본에 이미 있던 단어를
    반복한 것(`4 segundos, 5 segundos…`를 늘린 경우)은 새 내용이 아니라서 뺀다."""
    old, new = _words(original), _words(suggested)
    known = set(old)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    added: List[str] = []
    for op, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if op in ("insert", "replace"):
            added.extend(w for w in new[j1:j2] if w not in known)
    return added


def _overlap_with_neighbor(added: List[str], neighbor_text: str) -> Optional[List[str]]:
    """추가분이 이웃과 겹치면 겹친 단어(또는 문장) 목록을, 아니면 None."""
    if not added:
        return None
    neighbor_words = set(_words(neighbor_text))
    if len(added) <= MAX_SHORT_ADDITION_WORDS:
        sentences = [_words(s) for s in _SENTENCE_SPLIT_RE.split(_strip_tags(neighbor_text))]
        if added in [s for s in sentences if s]:
            return [" ".join(added)]
    long_added = [w for w in added if len(w) >= MIN_LONG_WORD_CHARS]
    shared = [w for w in dict.fromkeys(long_added) if w in neighbor_words]
    if shared and len(shared) / len(set(long_added)) >= MIN_LONG_WORD_SHARE:
        return shared
    return None


def compute_neighbor_warning(
    original_text: str, suggested_text: str, before_text: Optional[str], after_text: Optional[str],
) -> Optional[Dict]:
    """앞뒤 번역 큐와 겹치는 추가분이 있으면 {side, neighbor_text, words}를, 없으면 None.
    앞뒤 모두 겹치면 겹친 단어가 더 많은 쪽을 돌려준다."""
    added = _added_words(original_text, suggested_text)
    best: Optional[Dict] = None
    for side, neighbor in (("before", before_text), ("after", after_text)):
        if not neighbor:
            continue
        shared = _overlap_with_neighbor(added, neighbor)
        if shared and (best is None or len(shared) > len(best["words"])):
            best = {"side": side, "neighbor_text": neighbor, "words": shared}
    return best

