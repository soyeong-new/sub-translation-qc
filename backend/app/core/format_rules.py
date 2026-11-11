"""줄 길이/연속 온점 같은 언어 무관 자막 포맷팅 규칙을 검사·보정하는 모듈."""

import re
import textwrap
from typing import List, Optional, Tuple
from app.schemas import AlignedPair, FormatViolation

MAX_LINE_CHARS = 42
MAX_LINES = 2
# 42자는 맞추려는 목표, 줄당 이 값까지 더한 길이는 허용(위반으로 안 친다 — 자연스러운 끊김이 없는
# 줄을 한두 글자 때문에 어색하게 재배치하거나 큐를 늘리지 않으려고). 읽기 속도로 줄인 더 작은 한도에는 적용 안 한다.
LINE_TOLERANCE = 3
# 자막 한 줄을 초당 몇 자까지 읽을 수 있다고 볼지 — 정적인 줄당 글자수
# 제약만으로는 큐 노출 시간이 아주 짧을 때(예: 1.2초) 읽기엔 너무 많은
# 글자가 통과할 수 있다(실제로 발견됨: 짧은 큐에 두 문장이 합쳐져 들어감).
READING_SPEED_CPS = 20
_ELLIPSIS_RE = re.compile(r"\.{4,}|(?<!\.)\.\.(?!\.)")  # 4개 이상 또는 정확히 2개 → 3개
# 줄바꿈 위치 비용 — 낮을수록 자연스럽다. 문장 끝 > 대사 전환 > 쉼표류 > 접속사 앞 >
# 전치사 앞 > 일반 단어 사이. 길이 균형·한 단어 고아 줄 감점(최대 6)이 아무리 붙어도
# 구두점 지점(최대 8)이 일반 단어 사이(10)를 이기도록 간격을 잡았다.
# 부호 뒤에 닫는 태그·따옴표가 붙을 수 있다(`</i>`). `[.!?…]+`로 묶은 건 "..." 같은
# 말줄임표 내부에서 끊기는 걸 막기 위함(연속된 덩어리를 한 지점으로 봄).
_CLOSERS = r"(?:</\w+>|[\"'”’»)\]])*"
_SENTENCE_END_RE = re.compile(r"[.!?…]+" + _CLOSERS + r"$")
_COMMA_RE = re.compile(r"[,;:]" + _CLOSERS + r"$")
_DIALOGUE_DASH_RE = re.compile(r"-(?!-)")  # "--"(말 끊김)는 대사 전환이 아니다
_COST_SENTENCE_END, _COST_DASH, _COST_COMMA = 0, 1, 2
_COST_CONJUNCTION, _COST_PREPOSITION, _COST_WORD = 4, 7, 10
_COST_FORBIDDEN = 50  # 끊으면 안 되는 위치 — 후보가 이것뿐이어도 실패시키지 않으려 제외 대신 큰 감점
# 줄 첫머리의 대사 대시 — "--"(말 끊김)는 제외하려고 뒤에 "-"가 아닐 것을 요구한다.
_SPEAKER_DASH_RE = re.compile(r"^-(?!-)[ \t]*", re.MULTILINE)


def _clean(word: str) -> str:
    return word.lower().replace("’", "'").strip("¿¡\"'«“([").rstrip("\"'»”)],.;:!?…")


def _tokens(flat: str) -> List[str]:
    """줄바꿈 후보 단위. 프랑스어 "dire ?"처럼 앞에 공백이 있는 부호(? ! : ;)는 앞 단어에,
    단독 "¿"·"¡"는 뒤 단어에 붙여 한 덩어리로 다룬다 — 공백에서 끊으면 부호만 다음 줄에
    남는다. 글자 자체는 바꾸지 않는다(출력 SRT 그대로)."""
    out: List[str] = []
    for w in flat.split(" "):
        if out and re.fullmatch(r"[?!:;]+", w):
            out[-1] += " " + w
        elif out and re.fullmatch(r"[¿¡]+", out[-1]):
            out[-1] += " " + w
        else:
            out.append(w)
    return out


def _phrase_cuts(words: List[str], phrases: List[str]) -> set:
    """words 안에서 no_break_phrases("parce que" 등) 한가운데에 있는 경계 인덱스(i = words[i-1]|words[i])."""
    cleaned, cuts = [_clean(w) for w in words], set()
    for phrase in phrases:
        parts = [_clean(x) for x in phrase.split()]
        for start in range(len(words) - len(parts) + 1):
            if cleaned[start:start + len(parts)] == parts:
                cuts.update(range(start + 1, start + len(parts)))
    return cuts


def _break_cost(prev: str, nxt: str, rules: dict) -> int:
    """prev와 nxt 단어 사이를 끊는 비용. rules는 언어 프로파일의 line_break
    (conjunctions/prepositions/no_break_after) — 없으면 구두점·고유명사·숫자 규칙만 쓴다."""
    if _SENTENCE_END_RE.search(prev):
        return _COST_SENTENCE_END
    if _DIALOGUE_DASH_RE.match(nxt):
        return _COST_DASH
    if _COMMA_RE.search(prev):
        return _COST_COMMA
    p, n = _clean(prev), _clean(nxt)
    if (p in rules.get("no_break_after", ()) or p in rules.get("prepositions", ())  # "para / que"
            or (prev[:1].isupper() and nxt[:1].isupper())  # "Bool Gom / Gang"
            or any(c.isdigit() for c in prev + nxt)):      # 숫자와 단위 사이
        return _COST_FORBIDDEN
    if n in rules.get("conjunctions", ()):
        return _COST_CONJUNCTION
    if n in rules.get("prepositions", ()):
        return _COST_PREPOSITION
    return _COST_WORD


def _limit(max_chars: int) -> int:
    return max_chars + LINE_TOLERANCE if max_chars >= MAX_LINE_CHARS else max_chars


def rewrap_line(text: str, max_chars: int = MAX_LINE_CHARS, max_lines: int = MAX_LINES,
                 rules: Optional[dict] = None, avoid_forbidden: bool = False) -> Optional[str]:
    """줄바꿈 위치만 바꿔서 위반을 해소할 수 있으면(내용 자체는 안 늘어난
    경우) 그 결과를 반환한다. 두 줄이 모두 max_chars 안에 들어가는 모든 단어
    경계를 후보로 모아 _break_cost가 가장 낮은 곳에서 끊는다(동점이면 첫 줄을 더
    채우는 뒤쪽). 줄 길이가 크게 차이 나거나 한 단어만 남는 줄은 감점한다.
    재배치만으로 안 되면(문장 자체가 너무 길어 2줄×42자 안에 못 들어가는
    경우) None을 반환해 호출자가 LLM 축약으로 폴백하게 한다 — 단어를
    쪼개지 않으므로, 한 단어 자체가 max_chars보다 길면 항상 실패한다(그 경우도
    LLM이 필요한 상황이라 맞는 동작). avoid_forbidden이면 끊을 수 있는 자리가 금지 위치
    (관사 뒤·전치사 뒤 등)뿐일 때도 None — 어색하게 끊느니 호출자가 다른 방법(큐 분할)을 쓴다."""
    # str.split()은 NBSP(\u00A0)도 공백으로 갈라버린다 — 일반 공백류만 정규화해 NBSP는 보존한다
    flat = " ".join(w for w in re.split(r"[ \t\r\n]+", text) if w)
    if not flat:
        return None
    rules = rules or {}
    words = _tokens(flat)
    cuts = _phrase_cuts(words, rules.get("no_break_phrases", ()))
    best = None  # (cost, 첫 줄 길이, 결과)
    if max_lines >= 2:
        for i in range(1, len(words)):
            first, second = " ".join(words[:i]), " ".join(words[i:])
            if len(first) > max_chars or len(second) > max_chars:
                continue
            cost = _break_cost(words[i - 1], words[i], rules) + (_COST_FORBIDDEN if i in cuts else 0)
            cost += round(3 * abs(len(first) - len(second)) / max_chars)
            if min(i, len(words) - i) == 1:
                cost += 3
            if best is None or (cost, -len(first)) < best[:2]:
                best = (cost, -len(first), f"{first}\n{second}")
    if best:
        return None if avoid_forbidden and best[0] >= _COST_FORBIDDEN else best[2]
    lines = textwrap.wrap(flat, max_chars, break_long_words=False, break_on_hyphens=False)
    if lines and len(lines) <= max_lines and all(len(ln) <= max_chars for ln in lines):
        return "\n".join(lines)
    return None


def split_in_two(text: str, rules: Optional[dict] = None,
                 max_cost: Optional[int] = None) -> Optional[Tuple[str, str]]:
    """큐를 둘로 나눌 위치를 rewrap_line과 같은 비용(_break_cost)으로 골라 (앞, 뒤) 문구를
    반환한다. 두 조각이 각각 2줄(2×MAX_LINE_CHARS) 안에 들어가는 후보를 우선하고, 길이가
    비슷할수록 유리하다(시간을 글자수 비율로 나누므로). max_cost를 주면 그 비용 이하인
    경계(예: 문장 끝·대사 전환·쉼표)만 쓰고, 없으면 None. 단어가 하나뿐이면 None."""
    flat = " ".join(w for w in re.split(r"[ \t\r\n]+", text) if w)
    words = _tokens(flat)
    cuts = _phrase_cuts(words, (rules or {}).get("no_break_phrases", ()))
    best = None
    for i in range(1, len(words)):
        cost = _break_cost(words[i - 1], words[i], rules or {}) + (_COST_FORBIDDEN if i in cuts else 0)
        if max_cost is not None and cost > max_cost:
            continue
        first, second = " ".join(words[:i]), " ".join(words[i:])
        fits = max(len(first), len(second)) <= MAX_LINES * MAX_LINE_CHARS
        score = (0 if fits else 1, cost + round(2 * abs(len(first) - len(second)) / len(flat)), -len(first))
        if best is None or score < best[0]:
            best = (score, first, second)
    return (best[1], best[2]) if best else None


def violates_line_length(text: str, max_chars: int = MAX_LINE_CHARS,
                          max_lines: int = MAX_LINES) -> bool:
    """check_line_length의 단일 텍스트 버전 — pairs/Segment 없이 검수자가
    승인/입력한 문구 하나만 검사해야 하는 곳(review-action, pick)에서 쓴다."""
    lines = text.split("\n")
    return len(lines) > max_lines or any(len(ln) > _limit(max_chars) for ln in lines)


def final_text_reason(suggested: str, final: str) -> Optional[str]:
    """승인 때 저장된 최종 텍스트가 AI 제안문과 달라진 이유를 저장값만으로 추정한다(DB에 이유를
    따로 저장하지 않는다). 공백·줄바꿈만 다르면 "line_wrap", 단어가 다른데 제안문이 글자수
    제한 안이면 글자수가 원인일 수 없으므로 "glossary", 제안문이 제한을 넘으면 둘 중 하나라
    "line_limit_or_glossary". 같거나 비어 있으면 None."""
    if not suggested or not final or suggested == final:
        return None
    if suggested.split() == final.split():
        return "line_wrap"
    return "line_limit_or_glossary" if violates_line_length(suggested) else "glossary"


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
        if violates_line_length(pair.target.text):
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
    있었다(줄당 42자 제약은 통과하지만 읽을 시간이 부족한 경우)."""
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


def normalize_speaker_dash(text: str) -> str:
    """2인 대사의 줄 첫머리 "-" 뒤 공백을 "- "로 통일한다. 대시가 없는 줄은
    그대로 둔다. 언어 무관 — 모든 대상언어에 공백을 두기로 정함(넷플릭스
    영어 가이드는 공백 없음이지만 프로젝트 기준을 우선한다)."""
    return _SPEAKER_DASH_RE.sub("- ", text)


def fix_ellipsis(text: str) -> Tuple[str, bool]:
    fixed = _ELLIPSIS_RE.sub("...", text)
    return fixed, fixed != text


def check_ellipsis(pairs: List[AlignedPair]) -> List[FormatViolation]:
    """온점 4개 이상 위반을 감지하고 fixed_text에 보정안을 담아 반환한다 —
    텍스트 자체는 호출자(pipeline)가 바로 반영한다. 판단 여지가 없는 기계적
    규칙이라 사람 승인 없이 자동 적용되지만, 검수자 진행률 카운팅
    (ReviewView.jsx)에서는 이 자동보정 finding을 실제 검수 대상에서 제외한다."""
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
