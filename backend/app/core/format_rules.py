"""줄 길이/연속 온점 같은 언어 무관 자막 포맷팅 규칙을 검사·보정하는 모듈."""

import re
import textwrap
from typing import List, Optional, Tuple
from app.schemas import AlignedPair, FormatViolation

MAX_LINE_CHARS = 50
MAX_LINES = 2
_ELLIPSIS_RE = re.compile(r"\.{4,}")


def rewrap_line(text: str, max_chars: int = MAX_LINE_CHARS,
                 max_lines: int = MAX_LINES) -> Optional[str]:
    """줄바꿈 위치만 바꿔서 위반을 해소할 수 있으면(내용 자체는 안 늘어난
    경우) 그 결과를 반환한다. 재배치만으로 안 되면(문장 자체가 너무 길어
    2줄×50자 안에 못 들어가는 경우) None을 반환해 호출자가 LLM 축약으로
    폴백하게 한다 — 단어를 쪼개지 않으므로(break_long_words=False), 한
    단어 자체가 max_chars보다 길면 항상 실패한다(그 경우도 LLM이 필요한
    상황이라 맞는 동작)."""
    flat = " ".join(text.split())
    if not flat:
        return None
    lines = textwrap.wrap(flat, max_chars, break_long_words=False, break_on_hyphens=False)
    if lines and len(lines) <= max_lines and all(len(ln) <= max_chars for ln in lines):
        return "\n".join(lines)
    return None


def check_line_length(pairs: List[AlignedPair]) -> List[FormatViolation]:
    """최초 체크(design §5-1의 1번 지점) 및 export 직전 안전망(3번 지점)에서
    호출된다. 자동 수정은 하지 않고 finding만 만든다 — 줄바꿈 재배치로 될
    수도, 의미를 줄이는 LLM 교정이 필요할 수도 있어 호출자(safety_net)가
    rewrap_line을 먼저 시도한 뒤 판단한다."""
    violations = []
    for pair in pairs:
        if pair.target is None:
            continue
        lines = pair.target.text.split("\n")
        if len(lines) > MAX_LINES or any(len(ln) > MAX_LINE_CHARS for ln in lines):
            violations.append(FormatViolation(
                segment_id=pair.id, rule="line_length",
                detail=f"{len(lines)}줄, 최대 줄 길이 {max(len(ln) for ln in lines)}자",
                original_text=pair.target.text,
            ))
    return violations


def fix_ellipsis(text: str) -> Tuple[str, bool]:
    fixed = _ELLIPSIS_RE.sub("...", text)
    return fixed, fixed != text


def check_ellipsis(pairs: List[AlignedPair]) -> List[FormatViolation]:
    """온점 4개 이상은 판단 여지가 없는 기계적 위반이라 바로 자동 보정하고,
    fixed_text에 보정된 결과를 담아 반환한다 (검수자 확인 불필요, design §5-1)."""
    violations = []
    for pair in pairs:
        if pair.target is None:
            continue
        fixed, changed = fix_ellipsis(pair.target.text)
        if changed:
            violations.append(FormatViolation(
                segment_id=pair.id, rule="ellipsis",
                detail="연속 온점 4개 이상 감지",
                auto_fixed=True, fixed_text=fixed,
                # 이 체크가 호출된 시점의 텍스트를 스냅샷으로 남긴다. 파이프라인은
                # 이 함수를 여러 체크포인트(최초, GPT 이후 최종 재체크)에서
                # 호출하므로, 나중에 다른 시점의 최종 텍스트로부터 되짚어
                # 재구성하면 이 체크포인트에서 실제로 무엇이 "고치기 전"이었는지가
                # 사라진다.
                original_text=pair.target.text,
            ))
    return violations
