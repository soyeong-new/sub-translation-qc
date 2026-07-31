"""사전 필터로 민감어 후보를 걸러낸 뒤 LLM으로 문맥을 판단하는 모듈."""

from typing import List
from pydantic import ValidationError
from app.schemas import AlignedPair, Finding
from app.providers.base import ModelProvider


def _dictionary_hits(pairs: List[AlignedPair], terms: List[str]) -> List[dict]:
    hits = []
    for pair in pairs:
        if pair.target is None:
            continue
        text_lower = pair.target.text.lower()
        for term in terms:
            if term.lower() in text_lower:
                hits.append({"segment_id": pair.id, "term": term})
                break
    return hits


async def run_sensitivity_check(pairs: List[AlignedPair], terms: List[str],
                                 provider: ModelProvider,
                                 target_version_id: str) -> List[Finding]:
    """사전 1차 필터로 후보를 줄인 뒤에만 LLM을 호출한다 — 점수 감점이 아니라
    별도 플래그 섹션으로 표시된다 (design §5, §6)."""
    hits = _dictionary_hits(pairs, terms)
    if not hits:
        return []
    pair_dicts = [
        {"id": p.id, "target_text": p.target.text if p.target else ""}
        for p in pairs if p.target is not None
    ]
    raw = await provider.check_sensitivity(pair_dicts, hits)
    pair_by_id = {p.id: p for p in pairs}
    findings = []
    # translation_review.py와 동일한 이유: 같은 모델이 같은 segment_id에 대해
    # sensitivity finding을 두 개 이상 돌려주면 id가 충돌해 저장 시
    # IntegrityError가 난다. (segment_id, model) 조합별 등장 횟수를 세어
    # 두 번째부터는 _2, _3... 접미사를 붙인다.
    id_occurrence_counts: dict = {}
    for r in raw:
        try:
            pair = pair_by_id[r["segment_id"]]
            model = r.get("model")
            id_suffix = f"_{model}" if model else ""
            dedup_key = (r["segment_id"], model)
            id_occurrence_counts[dedup_key] = id_occurrence_counts.get(dedup_key, 0) + 1
            ordinal = id_occurrence_counts[dedup_key]
            ordinal_suffix = f"_{ordinal}" if ordinal > 1 else ""
            findings.append(Finding(
                id=f"finding_{r['segment_id']}_sensitivity{id_suffix}{ordinal_suffix}",
                target_version_id=target_version_id,
                segment_id=r["segment_id"], category="sensitivity",
                description=r["description"],
                original_text=pair.target.text if pair.target else "",
                suggested_text="", confidence=1.0, source="llm",
                model=model,
            ))
        except (KeyError, ValidationError):
            # Skip malformed items (unknown segment_id, missing required fields,
            # or invalid field values that fail Finding validation)
            continue
    return findings
