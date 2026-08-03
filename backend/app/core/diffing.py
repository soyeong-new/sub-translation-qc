"""LLM 교정 결과(변경된 세그먼트만 담긴 리스트)를 diff 기반 Finding으로 변환하는 모듈."""

from typing import List
from app.schemas import AlignedPair, Finding


def _dedupe_by_segment_id(corrections: List[dict]) -> List[dict]:
    """동일 segment_id에 대해 correction이 두 개 이상 오면 첫 번째만 채택한다.

    findings_from_corrections(검수자에게 보여줄 제안)와 apply_corrections(다음
    단계/최종 텍스트에 실제로 반영되는 값)가 이 dedup 규칙을 공유하지 않으면,
    검수자가 화면에서 보는 suggested_text와 실제로 반영·export되는 텍스트가
    서로 다른 correction에서 나와 어긋날 수 있다. 두 함수가 반드시 같은
    규칙을 쓰도록 dedup 로직을 이 한 곳에만 둔다."""
    seen: set = set()
    deduped = []
    for c in corrections:
        if c["segment_id"] in seen:
            continue
        seen.add(c["segment_id"])
        deduped.append(c)
    return deduped


def findings_from_corrections(target_version_id: str, pairs: List[AlignedPair],
                               corrections: List[dict], stage: str) -> List[Finding]:
    """corrections는 [{"segment_id","category","corrected_text","description"}] 형태이며,
    실제로 원문과 달라진 세그먼트만 담겨 있다는 게 클라이언트 프롬프트의 계약이다.
    그래도 LLM이 이 계약을 어기고 동일 텍스트나 같은 세그먼트를 중복으로 돌려줄
    가능성에 대비해 여기서 다시 걸러낸다(첫 번째 correction을 채택)."""
    pair_by_id = {p.id: p for p in pairs}
    findings = []
    for c in _dedupe_by_segment_id(corrections):
        pair = pair_by_id.get(c["segment_id"])
        if pair is None or pair.target is None:
            continue
        original_text = pair.target.text
        corrected_text = c["corrected_text"]
        if corrected_text == original_text:
            continue
        findings.append(Finding(
            id=f"finding_{c['segment_id']}_{stage}_{c['category']}",
            target_version_id=target_version_id, segment_id=c["segment_id"],
            category=c["category"], description=c["description"],
            original_text=original_text, suggested_text=corrected_text,
            confidence=1.0, source="llm", model=stage,
        ))
    return findings


def apply_corrections(pairs: List[AlignedPair], corrections: List[dict]) -> None:
    """다음 단계(GPT 2차)가 이전 단계(Claude 1차)의 결과를 이어받아 작업할 수
    있도록, corrected_text를 pair.target.text에 그대로 반영한다(in-place).

    findings_from_corrections와 동일하게 _dedupe_by_segment_id로 첫 번째
    correction을 채택한다 — 두 함수가 서로 다른 correction을 채택하면 검수자가
    보는 제안(finding)과 실제 반영되는 텍스트가 어긋나게 된다."""
    corrected_by_id = {
        c["segment_id"]: c["corrected_text"] for c in _dedupe_by_segment_id(corrections)
    }
    for pair in pairs:
        if pair.id in corrected_by_id and pair.target is not None:
            pair.target.text = corrected_by_id[pair.id]
