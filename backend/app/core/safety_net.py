"""최종 안전망: 글자수 위반 줄만 소규모 LLM 호출로 재교정하는 모듈 (design §S4)."""

from typing import List, Optional, Tuple
from app.schemas import AlignedPair, Finding, FormatViolation
from app.providers.base import ModelProvider
from app.core.format_rules import (
    MAX_LINE_CHARS, MAX_LINES, rewrap_line, violates_line_length,
)


async def enforce_line_length(text: str, provider: ModelProvider,
                               max_chars: int = MAX_LINE_CHARS,
                               max_lines: int = MAX_LINES,
                               rules: Optional[dict] = None) -> Tuple[str, bool]:
    """텍스트 하나가 자막 글자수 제약(줄당 max_chars자, 최대 max_lines줄)을
    위반하면 자동으로 줄인다 — 먼저 rewrap_line(줄바꿈 재배치만, 내용 안 바뀜)을
    시도하고, 그걸로 안 되면 LLM 축약(shrink_line)으로 폴백한다. LLM은 지시를
    완벽히 안 지킬 수 있으므로(자연스러운 위치에 줄바꿈을 못 넣거나, 줄인 뒤에도
    여전히 넘칠 수 있음) 결과를 다시 검증해서, 그래도 위반이면 마지막으로 한 번
    더 rewrap_line을 시도한다(LLM이 줄인 내용 자체는 유지하고 줄바꿈 위치만
    기계적으로 재배치). 그마저 실패하면(단어 자체가 너무 긴 극단적인 경우)
    LLM 결과를 그대로 둔다 — 더 손쓸 방법이 없다. 반환값은 (최종 텍스트,
    실제로 바뀌었는지). max_chars/max_lines은 기본은 정적 줄당 글자수
    제약이지만, 호출자(shrink_violating_lines)가 노출 시간이 짧은 큐엔 더
    엄격한 값을 넘겨 읽기 속도 위반도 같은 경로로 축약한다. rules는 언어
    프로파일의 line_break(rewrap_line이 줄바꿈 위치 비용을 매기는 단어 목록)."""
    if not violates_line_length(text, max_chars, max_lines):
        return text, False
    rewrapped = rewrap_line(text, max_chars, max_lines, rules)
    if rewrapped is not None:
        return rewrapped, True
    shrunk = await provider.shrink_line(text, max_chars, max_lines)
    if violates_line_length(shrunk, max_chars, max_lines):
        re_rewrapped = rewrap_line(shrunk, max_chars, max_lines, rules)
        if re_rewrapped is not None:
            shrunk = re_rewrapped
    return shrunk, True


def _dedupe_by_segment(violations: List[FormatViolation]) -> List[FormatViolation]:
    """같은 세그먼트가 두 규칙(line_length·reading_speed)에 동시에 걸릴 수
    있다 — finding id가 segment_id 기준이라 그대로 두면 PK가 충돌한다.
    세그먼트당 하나만 남긴다. 어느 규칙이 실제로 걸렸었는지는
    _rules_by_segment로 별도 유지하므로, 여기서 하나를 버려도 정보가
    사라지지 않는다."""
    seen: dict = {}
    for v in violations:
        seen.setdefault(v.segment_id, v)
    return list(seen.values())


async def shrink_violating_lines(pairs: List[AlignedPair],
                                  violations: List[FormatViolation],
                                  provider: ModelProvider,
                                  target_version_id: str,
                                  rules: Optional[dict] = None) -> List[Finding]:
    """LLM을 호출하지 않는다(provider는 호출부 호환용으로만 남아 있다). 위반된 세그먼트마다
    rewrap_line으로 줄바꿈만 재배치해 pair.target.text에 바로 반영하고 자동
    승인한다 — 판단 여지가 없는 기계적 규칙이라 사람 승인을 기다리지 않지만,
    검수자 진행률 카운팅(ReviewView.jsx)에서는 사람이 손댄 적 없는 이 finding을
    제외해 실제 검수 대상과 섞이지 않게 한다.

    최대 글자수는 정적 줄당 글자수 제약(MAX_LINE_CHARS)을 사용한다.
    읽기 속도는 화면에 실제로 입혀서 확인하므로 여기서는 고려하지 않는다."""
    violations = _dedupe_by_segment(violations)
    if not violations:
        return []
    pair_by_id = {p.id: p for p in pairs}

    findings = []
    for v in violations:
        pair = pair_by_id.get(v.segment_id)
        if pair is None or pair.target is None:
            continue
        max_chars = MAX_LINE_CHARS

        original_text = pair.target.text
        rewrapped = rewrap_line(original_text, max_chars, MAX_LINES, rules, avoid_forbidden=True)
        if rewrapped is None:
            # 줄바꿈만으로 안 풀리면 LLM으로 줄이지 않는다 — 내용이 사라지기 때문이다. 저장 직후
            # auto_split_overlong_segments가 큐를 나눠 풀고, 못 나누는 줄은 그대로 남아 export
            # 경고(safety_net_check)로 검수자에게 보인다.
            continue

        pair.target.text = rewrapped
        findings.append(Finding(
            id=f"finding_{v.segment_id}_safety_net_formatting",
            target_version_id=target_version_id, segment_id=v.segment_id,
            category="formatting", description=f"자막 제약 위반, 줄바꿈만 재배치: {v.detail}",
            original_text=original_text, suggested_text=rewrapped,
            confidence=1.0, source="rule", model="자동재배치",
            status="approved", final_text=rewrapped,
        ))
    return findings
