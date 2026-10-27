"""확정된 성별과 번역문 단어의 성이 어긋난 줄을 코드로 찾는 모듈.

확정된 성별은 AI 검증기에 정보로만 넘기고 문장은 다시 쓰지 않는다(5e406b1) —
그런데 검증기가 어긋남을 놓치면(실제로 acalorado/ebria/experto 3건이 그대로 남았다)
아무도 잡지 못한다. 그래서 찾기는 이 모듈이 spaCy 형태소 정보로 하고, 고친 문장은
LLM이 쓰되(검수자가 대상언어를 모르므로 한국어 역번역이 붙는 기존 finding 흐름을 탄다)
여기서 "성 어미만 바뀌었는지" 검사해 문장이 통째로 바뀐 결과는 버린다."""

import os
import re
from typing import Optional

_SPACY_GENDER = {"male": "Masc", "female": "Fem"}
_WORD_RE = re.compile(r"\w+")
_MAX_CHANGED_WORDS = 3   # 성 어미가 바뀌는 단어 + 그 단어와 일치해야 하는 한두 단어


def _agrees_with_noun(token) -> bool:
    """사람이 아니라 앞뒤 명사에 성을 맞춘 형용사("mi última oportunidad"의 última)는
    화자 성별이 바뀌어도 그대로라 어긋남으로 보면 오탐이다."""
    head = token.head
    return head.pos_ in ("NOUN", "PROPN") and head.morph.get("Gender") == token.morph.get("Gender")


def find_gender_mismatches(pairs: list, resolved_registers: dict, nlp, skip_ids=()) -> dict:
    """{pair_id: [{"words": [어긋난 단어], "referent", "gender"}]}. gender_groups가 있는
    줄만 본다(단일 gender만 확정된 줄은 어느 단어에 적용할지 정보가 없다).
    그룹의 words 중 번역문에서 spaCy가 읽은 성이 확정된 성별과 다른 것만 담는다 —
    성을 읽을 수 없는 단어(성별 불변 형태)는 어긋남으로 보지 않는다."""
    mismatches: dict = {}
    for pair in pairs:
        if pair.target is None or pair.id in skip_ids:
            continue
        groups = (resolved_registers.get(pair.id) or {}).get("gender_groups")
        if not groups:
            continue
        doc = nlp(pair.target.text)
        bad_groups = []
        for group in groups:
            expected = _SPACY_GENDER.get(group.get("gender"))
            words = {w.lower() for w in group.get("words") or []}
            bad = [t.text for t in doc
                   if expected and t.text.lower() in words
                   and t.morph.get("Gender") and expected not in t.morph.get("Gender")
                   and not _agrees_with_noun(t)]
            if bad:
                bad_groups.append({**group, "words": list(dict.fromkeys(bad))})
        if bad_groups:
            mismatches[pair.id] = bad_groups
    return mismatches


def _same_stem(old: str, new: str) -> bool:
    prefix = len(os.path.commonprefix([old.lower(), new.lower()]))
    return prefix >= max(3, min(len(old), len(new)) - 2)


def only_inflection_changed(before: str, after: str, words: list) -> bool:
    """LLM이 고친 문장이 어긋난 단어(와 그에 일치하는 한두 단어)의 어미만 바꿨는지.
    구두점·띄어쓰기가 같고, 단어 수가 같고, 바뀐 단어가 3개 이하이며 그중 하나가
    어긋난 단어이고, 바뀐 단어는 모두 앞부분(어간)이 같아야 한다."""
    if _WORD_RE.sub("", before) != _WORD_RE.sub("", after):
        return False
    old, new = _WORD_RE.findall(before), _WORD_RE.findall(after)
    if len(old) != len(new):
        return False
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    targets = {w.lower() for w in words}
    return (0 < len(changed) <= _MAX_CHANGED_WORDS
            and any(a.lower() in targets for a, _ in changed)
            and all(_same_stem(a, b) for a, b in changed))
