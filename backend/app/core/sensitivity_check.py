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
    for r in raw:
        try:
            pair = pair_by_id[r["segment_id"]]
            findings.append(Finding(
                id=f"finding_{r['segment_id']}_sensitivity",
                target_version_id=target_version_id,
                segment_id=r["segment_id"], category="sensitivity",
                description=r["description"],
                original_text=pair.target.text if pair.target else "",
                suggested_text="", confidence=1.0, source="llm",
            ))
        except (KeyError, ValidationError):
            # Skip malformed items (unknown segment_id, missing required fields,
            # or invalid field values that fail Finding validation)
            continue
    return findings
