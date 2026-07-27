import re
from typing import List, Tuple
from app.schemas import AlignedPair, FormatViolation

MAX_LINE_CHARS = 50
MAX_LINES = 2
_ELLIPSIS_RE = re.compile(r"\.{4,}")


def check_line_length(pairs: List[AlignedPair]) -> List[FormatViolation]:
    """최초 체크(design §5-1의 1번 지점) 및 export 직전 안전망(3번 지점)에서
    호출된다. 자동 수정은 하지 않고 finding만 만든다 — 줄이는 작업은 의미
    보존이 필요해 LLM/검수자 판단이 필요하다."""
    violations = []
    for pair in pairs:
        if pair.target is None:
            continue
        lines = pair.target.text.split("\n")
        if len(lines) > MAX_LINES or any(len(ln) > MAX_LINE_CHARS for ln in lines):
            violations.append(FormatViolation(
                segment_id=pair.id, rule="line_length",
                detail=f"{len(lines)}줄, 최대 줄 길이 {max(len(ln) for ln in lines)}자",
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
            ))
    return violations
