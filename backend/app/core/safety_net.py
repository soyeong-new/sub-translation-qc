"""최종 안전망: 글자수 위반 줄만 소규모 LLM 호출로 재교정하는 모듈 (design §S4)."""

from typing import List, Tuple
from app.schemas import AlignedPair, Finding, FormatViolation
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES, rewrap_line, violates_line_length


async def enforce_line_length(text: str, provider: ModelProvider) -> Tuple[str, bool]:
    """텍스트 하나가 자막 글자수 제약(줄당 MAX_LINE_CHARS자, 최대 MAX_LINES줄)을
    위반하면 자동으로 줄인다 — 먼저 rewrap_line(줄바꿈 재배치만, 내용 안 바뀜)을
    시도하고, 그걸로 안 되면 LLM 축약(shrink_line)으로 폴백한다. LLM은 지시를
    완벽히 안 지킬 수 있으므로(자연스러운 위치에 줄바꿈을 못 넣거나, 줄인 뒤에도
    여전히 넘칠 수 있음) 결과를 다시 검증해서, 그래도 위반이면 마지막으로 한 번
    더 rewrap_line을 시도한다(LLM이 줄인 내용 자체는 유지하고 줄바꿈 위치만
    기계적으로 재배치). 그마저 실패하면(단어 자체가 너무 긴 극단적인 경우)
    LLM 결과를 그대로 둔다 — 더 손쓸 방법이 없다. 반환값은 (최종 텍스트,
    실제로 바뀌었는지)."""
    if not violates_line_length(text):
        return text, False
    rewrapped = rewrap_line(text, MAX_LINE_CHARS, MAX_LINES)
    if rewrapped is not None:
        return rewrapped, True
    shrunk = await provider.shrink_line(text, MAX_LINE_CHARS, MAX_LINES)
    if violates_line_length(shrunk):
        re_rewrapped = rewrap_line(shrunk, MAX_LINE_CHARS, MAX_LINES)
        if re_rewrapped is not None:
            shrunk = re_rewrapped
    return shrunk, True


async def shrink_violating_lines(pairs: List[AlignedPair],
                                  violations: List[FormatViolation],
                                  provider: ModelProvider,
                                  target_version_id: str) -> List[Finding]:
    """위반이 없으면 LLM을 전혀 호출하지 않는다. 위반된 세그먼트마다
    enforce_line_length로 줄이고, 그 결과를 pair.target.text에 즉시 반영한다
    — 이후 결과 저장/export가 이 최종 텍스트를 그대로 쓴다. rewrap_line만으로
    해결됐는지(규칙 기반) LLM까지 갔는지에 따라 finding의 source/model이
    갈린다(검수자가 "왜 바뀌었는지" 구분할 수 있게)."""
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
            shrunk_text, _ = await enforce_line_length(original_text, provider)
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
