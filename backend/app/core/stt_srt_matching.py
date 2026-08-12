"""STT의 실측 타이밍과 한국어 SRT의 검증된 텍스트를 매칭해 합치는 모듈.

한국어 SRT가 있어도 STT는 그대로 돌린다(오디오 실측). 한국어 SRT는 큐
단위 타임코드만 줘서 단어별 정확한 발화 시각을 모르고, STT는 정확한
타이밍은 알지만 텍스트를 잘못 들을 수 있다 — 이 둘을 맞바꿔, STT의
타이밍에 SRT의 검증된 텍스트를 얹는다(design
2026-08-12-korean-srt-stt-timing-match-design.md)."""

import difflib
import re
from typing import List, Optional, Tuple
from app.core.ingest import load_srt

_LEADING_DASH_RE = re.compile(r"^-\s*")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_PAREN_PREFIX_RE = re.compile(r"^\([^)]*\)\s*")
_PUNCT_RE = re.compile(r"[^\w가-힣]")


def _clean_korean_cue_lines(text: str) -> List[str]:
    """사용자 제공 한국어 SRT에는 효과음/노래(`[...]`, `♪...`)와 화자
    표기(`(이름) 대사`)가 섞여 있다 — 대사가 아닌 텍스트가 한국어 원문에
    섞이지 않게 걸러낸다. 화자 이름은 추출해 쓰지 않고 버린다(design
    §범위 밖 — 과거 인물 로스터 제거 이력과 같은 이유로, 화자를 안다고
    그 줄의 문법적 성별 지시 대상까지 아는 건 아니라서).

    실제 SRT에서는 한 큐 안에 화자가 둘 이상일 때 줄 앞에 "- "를 붙이고
    (예: "- (경리) 아닌데요\n- (순모) 어?"), 효과음/지문 대괄호가 대사와
    같은 줄에 붙기도 한다(예: "- (순모) [떨리는 목소리로] 현아"). 대괄호는
    줄 전체가 아니라 줄 어디에 있든 제거한다 — 화자 괄호만 줄 맨 앞
    접두어로 취급한다.

    "- " 표시가 있으면(화자 둘 이상이 같은 큐 시간대에 겹쳐 말함) 줄을
    하나로 이어붙이지 않고 발화별로 따로 반환한다 — 같은 시간대에 겹쳐/
    따로 말한 것을 순서대로 이어붙이면 한쪽에 억지로 이른 시각을, 다른
    쪽에 늦은 시각을 떠넘기게 된다. "- " 표시가 없으면(한 사람의 대사가
    화면 폭 때문에 여러 줄로 나뉜 경우) 하나로 합친다."""
    raw_lines = text.split("\n")
    is_multi_speaker = any(line.strip().startswith("-") for line in raw_lines)

    kept_lines = []
    for line in raw_lines:
        line = _LEADING_DASH_RE.sub("", line.strip()).strip()
        if not line or line.startswith("♪"):
            continue
        line = _BRACKET_RE.sub("", line).strip()
        line = _PAREN_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        kept_lines.append(line)

    if not kept_lines:
        return []
    if is_multi_speaker:
        return kept_lines
    return [" ".join(kept_lines)]


def _normalize_for_matching(word: str) -> str:
    """STT는 보통 문장부호가 없어서, 비교 시 SRT 쪽 문장부호를 제거해
    맞춘다 — "뭐야?"와 "뭐야"가 다른 토큰으로 갈리지 않게."""
    return _PUNCT_RE.sub("", word)


def _srt_words_from_path(korean_srt_path: str) -> List[dict]:
    """한국어 SRT를 단어 단위로 펼친다. 각 단어는 자기 원본 큐의
    [cue_start,cue_end]를 같이 들고 있다 — STT가 이 단어를 못 찾았을 때
    보간 폴백 범위로 쓴다."""
    words: List[dict] = []
    for cue in load_srt(korean_srt_path):
        for line_text in _clean_korean_cue_lines(cue.text):
            for w in line_text.split():
                words.append({"text": w, "cue_start": cue.start, "cue_end": cue.end})
    return words


def _interpolate_gap(gap_words: List[dict], left_time: float, right_time: float) -> List[dict]:
    """STT가 확정 못 한 연속 구간(gap_words)을 left_time~right_time 사이에서
    글자 수 비례로 보간한다 — 이전 버전(korean_words_from_srt)과 같은
    원리지만, 보간 범위가 SRT 큐 전체가 아니라 양옆 STT 실측 지점 사이의
    훨씬 좁은 구간이라 오차가 작다."""
    if not gap_words:
        return []
    total_chars = sum(len(w["text"]) for w in gap_words)
    duration = right_time - left_time
    result = []
    cursor = left_time
    for w in gap_words:
        share = duration * (len(w["text"]) / total_chars) if total_chars else duration / len(gap_words)
        word_end = cursor + share
        result.append({"start": cursor, "end": word_end, "text": w["text"]})
        cursor = word_end
    return result


def match_stt_words_to_korean_srt(stt_words: List[dict], korean_srt_path: str) -> List[dict]:
    """STT 결과(실측 타이밍)와 한국어 SRT(검증된 텍스트)를 매칭해 합친다.
    반환 모양은 STT transcribe()와 동일해([{"start","end","text"}]) 이후
    align()/detect_global_offset()을 무수정으로 재사용한다.

    difflib.SequenceMatcher(표준 라이브러리)로 두 단어 시퀀스의 일치
    구간을 찾는다 — STT와 SRT는 같은 발화를 각자 다르게 옮겨적은 것이라
    단어 개수·경계가 정확히 안 맞을 수 있다(STT가 못 들은 단어, 다른
    띄어쓰기, 추임새 등). 일치한 단어는 SRT 원문 텍스트(문장부호 포함) +
    STT의 실측 타임코드를 쓴다. 일치하지 않는 SRT 단어(STT가 못 들음)는
    양옆의 확실한 매칭 지점 사이에서 보간하고, 대응하는 SRT가 없는 STT
    단어(오인식/추임새)는 버린다."""
    srt_words = _srt_words_from_path(korean_srt_path)
    if not srt_words or not stt_words:
        return []

    stt_norm = [_normalize_for_matching(w["text"]) for w in stt_words]
    srt_norm = [_normalize_for_matching(w["text"]) for w in srt_words]

    matcher = difflib.SequenceMatcher(None, stt_norm, srt_norm, autojunk=False)
    confirmed: List[Optional[Tuple[float, float]]] = [None] * len(srt_words)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            stt_w = stt_words[block.a + k]
            confirmed[block.b + k] = (stt_w["start"], stt_w["end"])

    result: List[dict] = []
    i = 0
    n = len(srt_words)
    while i < n:
        if confirmed[i] is not None:
            start, end = confirmed[i]
            result.append({"start": start, "end": end, "text": srt_words[i]["text"]})
            i += 1
            continue
        j = i
        while j < n and confirmed[j] is None:
            j += 1
        left_time = confirmed[i - 1][1] if i > 0 else None
        right_time = confirmed[j][0] if j < n else None

        if left_time is None and right_time is None:
            left_time = srt_words[i]["cue_start"]
            right_time = srt_words[j - 1]["cue_end"]
        elif left_time is None:
            # 왼쪽에 확실한 앵커가 없다 — 큐 시작을 쓰되, 오른쪽의 실측
            # 앵커보다 늦어지지 않게 한다(실측값을 버리지 않기 위해).
            left_time = min(srt_words[i]["cue_start"], right_time)
        elif right_time is None:
            # 오른쪽에 확실한 앵커가 없다 — 큐 끝을 쓰되, 왼쪽의 실측
            # 앵커보다 빨라지지 않게 한다.
            right_time = max(srt_words[j - 1]["cue_end"], left_time)
        elif right_time <= left_time:
            # 양쪽 다 실측 앵커인데 뒤집힌 극단적 경우(STT 타임코드 자체가
            # 순서를 벗어남) — 폭 0으로 축소해 최소한 다음 확정 구간과
            # 안 겹치게 한다.
            right_time = left_time
        result.extend(_interpolate_gap(srt_words[i:j], left_time, right_time))
        i = j

    return result
