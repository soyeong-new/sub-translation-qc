"""큐 하나를 문장 경계에서 둘로 나누는 공용 함수 — 수동 분할 버튼과 (예정된) 자동 분할이 같이 쓴다."""

import re
from typing import List, Optional, Tuple
from app.core.format_rules import (
    READING_SPEED_CPS, _SPEAKER_DASH_RE, rewrap_line, split_in_two, violates_line_length,
)

MIN_CUE_SECONDS = 1.0  # 나눈 큐 하나의 최소 표시 시간
_TAG_RE = re.compile(r"</?\w+>")


def _visible_len(text: str) -> int:
    return len(_TAG_RE.sub("", text.replace("\n", " ")))


def _drop_lone_dash(piece: str) -> str:
    """조각에 대사 대시 줄이 하나뿐이면 대시를 없앤다 — 대시는 한 큐 안에서 화자가 바뀔 때
    쓰는 표시라, 나눈 뒤 화자가 한 명이면 필요 없다. 둘 이상이면 그대로 둔다."""
    if len(re.findall(r"(?:^|\s)-(?!-)", piece)) == 1:
        return _SPEAKER_DASH_RE.sub("", piece, count=1)
    return piece


def _fit_lines(piece: str, rules: Optional[dict]) -> str:
    """한 줄 허용 길이 이내면 그대로, 아니면 2줄로 재배치(안 되면 원문 유지 — 검수자가 본다)."""
    flat = " ".join(piece.split())
    if not violates_line_length(flat):
        return flat
    return rewrap_line(flat, rules=rules) or flat


def split_durations(weights: List[int], total: float) -> Optional[List[float]]:
    """글자수 비율로 시간을 나누되 각 큐를 MIN_CUE_SECONDS 이상으로 올리고, 모자란 만큼을
    나머지 큐에서 비율로 덜어낸다. 전체 시간이 최소 합보다 짧으면 None."""
    n = len(weights)
    if total < MIN_CUE_SECONDS * n:
        return None
    durs = [total * w / max(sum(weights), 1) for w in weights]
    short = [i for i, d in enumerate(durs) if d < MIN_CUE_SECONDS]
    if short:
        rest = [i for i in range(n) if i not in short]
        left = total - MIN_CUE_SECONDS * len(short)
        wr = sum(weights[i] for i in rest) or 1
        durs = [MIN_CUE_SECONDS if i in short else left * weights[i] / wr for i in range(n)]
    return durs


def split_cue(text: str, start: float, end: float, rules: Optional[dict] = None,
              max_cost: Optional[int] = None, check_cps: bool = False
              ) -> Optional[List[Tuple[float, float, str]]]:
    """text를 두 큐 [(start, end, text), ...]로 나눈다. 나눌 수 없으면(경계 없음, 시간이
    2×MIN_CUE_SECONDS보다 짧음, check_cps인데 어느 한쪽이 초당 READING_SPEED_CPS자 초과) None.
    시간은 글자수 비율, 두 큐는 맞닿는다(앞 큐 끝 = 뒤 큐 시작)."""
    halves = split_in_two(text, rules, max_cost)
    if halves is None:
        return None
    pieces = [_fit_lines(_drop_lone_dash(h), rules) for h in halves]
    weights = [_visible_len(p) for p in pieces]
    durs = split_durations(weights, end - start)
    if durs is None:
        return None
    if check_cps and any(w / d > READING_SPEED_CPS for w, d in zip(weights, durs)):
        return None
    cues, t = [], start
    for piece, dur in zip(pieces, durs):
        cues.append((t, t + dur, piece))
        t += dur
    cues[-1] = (cues[-1][0], end, cues[-1][2])  # 부동소수점 오차 없이 원래 끝에 맞춘다
    return cues
