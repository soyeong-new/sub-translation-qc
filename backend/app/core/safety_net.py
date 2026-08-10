"""최종 안전망: 글자수 위반 줄만 소규모 LLM 호출로 재교정하는 모듈 (design §S4)."""

from typing import List
from app.schemas import AlignedPair, Finding, FormatViolation
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES, rewrap_line


async def shrink_violating_lines(pairs: List[AlignedPair],
                                  violations: List[FormatViolation],
                                  provider: ModelProvider,
                                  target_version_id: str) -> List[Finding]:
    """위반이 없으면 LLM을 전혀 호출하지 않는다. 위반된 세그먼트마다 먼저
    rewrap_line으로 줄바꿈만 재배치해서 해결되는지 본다 — 내용을 하나도 안
    건드리는 기계적 수정이라 LLM보다 빠르고 안전하다(LLM 축약은 의미를
    줄이며 미묘하게 문구가 바뀔 위험이 있다). 재배치로 안 되면(문장 자체가
    너무 긴 경우) 그때만 LLM(shrink_line)으로 폴백한다. 결과를
    pair.target.text에 즉시 반영한다 — 이후 결과 저장/export가 이 최종
    텍스트를 그대로 쓴다."""
    if not violations:
        return []
    pair_by_id = {p.id: p for p in pairs}
    findings = []
    for v in violations:
        pair = pair_by_id.get(v.segment_id)
        if pair is None or pair.target is None:
            continue
        original_text = pair.target.text
        rewrapped = rewrap_line(original_text, MAX_LINE_CHARS, MAX_LINES)
        if rewrapped is not None:
            shrunk_text = rewrapped
            source, model, description = (
                "rule", "자동재배치", f"글자수 제약 위반, 줄바꿈만 재배치: {v.detail}")
        else:
            shrunk_text = await provider.shrink_line(original_text, MAX_LINE_CHARS, MAX_LINES)
            source, model, description = (
                "llm", "안전망", f"글자수 제약 위반 자동 축약: {v.detail}")
        pair.target.text = shrunk_text
        findings.append(Finding(
            id=f"finding_{v.segment_id}_safety_net_formatting",
            target_version_id=target_version_id, segment_id=v.segment_id,
            category="formatting", description=description,
            original_text=original_text, suggested_text=shrunk_text,
            confidence=1.0, source=source, model=model,
            status="approved", final_text=shrunk_text,
        ))
    return findings
