"""줄 길이/연속 온점 같은 언어 무관 자막 포맷팅 규칙을 검사·보정하는 모듈."""

import re
import textwrap
from typing import List, Optional, Tuple
from app.schemas import AlignedPair, FormatViolation

MAX_LINE_CHARS = 50
MAX_LINES = 2
# 자막 한 줄을 초당 몇 자까지 읽을 수 있다고 볼지 — 정적인 줄당 글자수
# 제약만으로는 큐 노출 시간이 아주 짧을 때(예: 1.2초) 읽기엔 너무 많은
# 글자가 통과할 수 있다(실제로 발견됨: 짧은 큐에 두 문장이 합쳐져 들어감).
READING_SPEED_CPS = 20
_ELLIPSIS_RE = re.compile(r"\.{4,}")
# 문장/절 경계 우선순위 — 단어 경계보다 이 지점에서 끊는 게 자연스럽다
# ("홍길동" / "입니다"처럼 의미 단위가 잘리는 걸 피함). 마침표류를 먼저
# 시도하고 안 되면 쉼표. `[.!?]+`로 묶은 건 "..." 같은 말줄임표 내부에서
# 끊기는 걸 막기 위함(온점 하나하나가 아니라 연속된 덩어리를 한 지점으로 봄).
_SENTENCE_END_RE = re.compile(r"[.!?]+\s*")
_COMMA_RE = re.compile(r",\s*")


def _best_punct_split(flat: str, max_chars: int, pattern: "re.Pattern") -> Optional[str]:
    """pattern이 매치되는 지점마다 그 앞뒤로 나눠보고, 두 줄 다 max_chars
    안에 들어가는 후보 중 가장 뒤쪽(=1번째 줄을 최대한 채우는) 지점을
    쓴다. 맞는 후보가 없으면 None."""
    best = None
    for m in pattern.finditer(flat):
        first, second = flat[:m.end()].strip(), flat[m.end():].strip()
        if first and second and len(first) <= max_chars and len(second) <= max_chars:
            best = (first, second)
    return "\n".join(best) if best else None


def rewrap_line(text: str, max_chars: int = MAX_LINE_CHARS,
                 max_lines: int = MAX_LINES) -> Optional[str]:
    """줄바꿈 위치만 바꿔서 위반을 해소할 수 있으면(내용 자체는 안 늘어난
    경우) 그 결과를 반환한다. 문장/절 경계에서 끊을 수 있으면 그걸 우선
    쓰고(자연스러움), 안 되면 textwrap의 단어 경계 줄바꿈으로 폴백한다.
    재배치만으로 안 되면(문장 자체가 너무 길어 2줄×50자 안에 못 들어가는
    경우) None을 반환해 호출자가 LLM 축약으로 폴백하게 한다 — 단어를
    쪼개지 않으므로(break_long_words=False), 한 단어 자체가 max_chars보다
    길면 항상 실패한다(그 경우도 LLM이 필요한 상황이라 맞는 동작)."""
    flat = " ".join(text.split())
    if not flat:
        return None
    if max_lines >= 2:
        for pattern in (_SENTENCE_END_RE, _COMMA_RE):
            result = _best_punct_split(flat, max_chars, pattern)
            if result is not None:
                return result
    lines = textwrap.wrap(flat, max_chars, break_long_words=False, break_on_hyphens=False)
    if lines and len(lines) <= max_lines and all(len(ln) <= max_chars for ln in lines):
        return "\n".join(lines)
    return None


def violates_line_length(text: str, max_chars: int = MAX_LINE_CHARS,
                          max_lines: int = MAX_LINES) -> bool:
    """check_line_length의 단일 텍스트 버전 — pairs/Segment 없이 검수자가
    승인/입력한 문구 하나만 검사해야 하는 곳(review-action, pick)에서 쓴다."""
    lines = text.split("\n")
    return len(lines) > max_lines or any(len(ln) > max_chars for ln in lines)


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


def reading_speed_char_budget(duration_seconds: float) -> int:
    """큐 노출 시간(초) 안에 읽을 수 있다고 볼 최대 글자수(공백 제외 안 함,
    줄바꿈 포함 전체 텍스트 기준)."""
    return max(1, int(duration_seconds * READING_SPEED_CPS))


def effective_max_chars(duration_seconds: float) -> int:
    """줄당 정적 글자수 제약(MAX_LINE_CHARS)과, 노출 시간 기준 읽기 속도
    예산을 줄 수(MAX_LINES)로 나눈 값 중 더 엄격한(작은) 쪽 — 자동 축약이
    실제로 적용할 줄당 글자수 한도다. 이 값을 쓰는 모든 곳(안전망 S4,
    검수자가 pending 제안을 승인하는 시점)이 같은 계산을 쓰게 해서, 어느
    경로로 최종 확정되든 읽기 속도 위반이 새어나가지 않게 한다."""
    if duration_seconds <= 0:
        return MAX_LINE_CHARS
    speed_max_chars = max(1, reading_speed_char_budget(duration_seconds) // MAX_LINES)
    return min(MAX_LINE_CHARS, speed_max_chars)


def check_reading_speed(pairs: List[AlignedPair]) -> List[FormatViolation]:
    """줄당 글자수 제약(check_line_length)과 별개로, 큐가 화면에 떠 있는
    시간 대비 글자수가 너무 많아 읽을 수 없는 경우를 잡는다 — 두 문장을
    합친 교정문이 1초짜리 큐 하나에 통째로 들어가는 사고가 실제로
    있었다(줄당 50자 제약은 통과하지만 읽을 시간이 부족한 경우)."""
    violations = []
    for pair in pairs:
        if pair.target is None:
            continue
        duration = pair.target.end - pair.target.start
        if duration <= 0:
            continue
        text_chars = len(pair.target.text.replace("\n", ""))
        budget = reading_speed_char_budget(duration)
        if text_chars > budget:
            violations.append(FormatViolation(
                segment_id=pair.id, rule="reading_speed",
                detail=f"{duration:.1f}초 노출에 {text_chars}자 (권장 최대 {budget}자, "
                       f"초당 {READING_SPEED_CPS}자 기준)",
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
