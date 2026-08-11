"""한국어 STT 단어와 대상언어(스페인어) 자막 세그먼트를 타임코드 기준으로 짝짓는 정렬 모듈."""

from bisect import bisect_right
from typing import List, Optional, Tuple
from app.schemas import SegmentText, AlignedPair

# 자막 큐로 안 흡수된 한국어 단어들(내레이션, 화면 밖 발화 등)을 문장처럼
# 묶기 위한 간격 기준 — 이 이하 간격이면 같은 발화의 연속으로 보고 하나로
# 합친다. ponytail: 고정값. 단어 사이 자연스러운 쉼(0.x초)보다는 크고, 서로
# 다른 발화 사이 침묵(1초 이상)보다는 작게 잡음 — 오탐이 잦아지면 조정.
ORPHAN_WORD_GAP_SECONDS = 1.0

# 전역 오프셋 탐색 범위/해상도 — 영상 앞부분을 잘라 올리는 경우(리캡·인트로
# 제거 등) 보통 초 단위 몇십 초~몇 분 정도가 흔하므로 ±10분까지 본다.
# 1차로 성긴 간격(1초)으로 훑어 대략적인 위치를 찾고, 2차로 그 근처를
# 촘촘하게(0.05초) 다시 훑어 정밀도를 올린다 — 처음부터 촘촘히 전체 범위를
# 훑으면 느리다.
OFFSET_SEARCH_RANGE_SECONDS = 600.0
OFFSET_COARSE_STEP_SECONDS = 1.0
OFFSET_FINE_RANGE_SECONDS = 1.5
OFFSET_FINE_STEP_SECONDS = 0.05
# 오프셋 보정을 실제로 적용할지 판단하는 기준 — 사소한 노이즈로 엉뚱한
# 오프셋을 적용하는 사고를 막는다. (1) 오프셋 0일 때 이미 어느 정도 맞고
# 있다면, 그보다 이 배율만큼 더 잘 맞아야 보정한다. (2) 오프셋 0일 때
# 아예 하나도 안 맞는 경우(우리가 고치려는 실제 상황)라도, 우연히 몇 개
# 단어만 걸린 걸 상수 오프셋으로 오인하지 않도록 최소 절대/비율 매칭
# 개수를 요구한다.
OFFSET_MIN_IMPROVEMENT_RATIO = 1.2
OFFSET_MIN_ABSOLUTE_MATCHES = 5
OFFSET_MIN_MATCH_FRACTION = 0.15


def _count_words_in_windows(midpoints: List[float], starts: List[float],
                             intervals: List[Tuple[float, float]]) -> int:
    count = 0
    for t in midpoints:
        i = bisect_right(starts, t) - 1
        if i >= 0 and intervals[i][0] <= t < intervals[i][1]:
            count += 1
    return count


def detect_global_offset(korean_words: List[SegmentText],
                          target_segments: List[SegmentText]) -> float:
    """한국어 STT와 대상언어 SRT 사이에 일정한(상수) 시간차가 있는지 찾는다
    — 영상 앞부분을 잘라 올렸는데 SRT는 안 자른 원본 기준일 때 이런 상수
    오프셋이 생긴다(사람마다 매번 다른 게 아니라, 한 영상 안에서는 처음부터
    끝까지 똑같은 초만큼 어긋난다). 이 값만큼 한국어 단어 타임코드를
    옮기면(그 뒤에 align()이 정상 동작) 문장 대 문장 재정렬 없이 통째로
    바로잡을 수 있다. 상수 오프셋이 없어 보이면(오프셋 0이 이미 최선이거나
    개선폭이 작으면) 0.0을 반환해 아무것도 안 바꾼다."""
    if not korean_words or not target_segments:
        return 0.0

    intervals = sorted((t.start, t.end) for t in target_segments)
    starts = [iv[0] for iv in intervals]
    midpoints = [(w.start + w.end) / 2 for w in korean_words]

    def score(offset: float) -> int:
        return _count_words_in_windows([m + offset for m in midpoints], starts, intervals)

    baseline = score(0.0)
    best_offset, best_score = 0.0, baseline

    offset = -OFFSET_SEARCH_RANGE_SECONDS
    while offset <= OFFSET_SEARCH_RANGE_SECONDS:
        s = score(offset)
        if s > best_score:
            best_score, best_offset = s, offset
        offset += OFFSET_COARSE_STEP_SECONDS

    offset = best_offset - OFFSET_FINE_RANGE_SECONDS
    end = best_offset + OFFSET_FINE_RANGE_SECONDS
    while offset <= end:
        s = score(offset)
        if s > best_score:
            best_score, best_offset = s, offset
        offset += OFFSET_FINE_STEP_SECONDS

    if best_offset == 0.0:
        return 0.0
    min_required = max(OFFSET_MIN_ABSOLUTE_MATCHES, len(korean_words) * OFFSET_MIN_MATCH_FRACTION)
    if best_score < min_required:
        return 0.0
    if baseline > 0 and best_score < baseline * OFFSET_MIN_IMPROVEMENT_RATIO:
        return 0.0
    return best_offset


def _midpoint_in(word: SegmentText, target: SegmentText) -> bool:
    midpoint = (word.start + word.end) / 2
    return target.start <= midpoint < target.end


def _merge_words(words: List[SegmentText]) -> SegmentText:
    return SegmentText(
        start=words[0].start, end=words[-1].end,
        text=" ".join(w.text for w in words),
    )


def _group_by_gap(words: List[SegmentText]) -> List[List[SegmentText]]:
    groups: List[List[SegmentText]] = []
    current: List[SegmentText] = []
    for w in words:
        if current and w.start - current[-1].end >= ORPHAN_WORD_GAP_SECONDS:
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def align(korean_words: List[SegmentText],
          target_segments: List[SegmentText]) -> List[AlignedPair]:
    """대상언어(스페인어) SRT 큐가 시간의 기준이다 — 영상도, 검수 화면의
    글자수 제약도 이 큐 단위로 움직이기 때문이다. 각 큐의 [start, end) 구간
    안에 중점(midpoint)이 들어오는 한국어 단어들을 그러모아 그 큐의
    한국어 텍스트로 삼는다(문장 대 문장 정렬이 아니라 단어를 SRT 큐 안에
    담는 방식) — 위스퍼가 침묵 기준으로 끊는 문장 경계와 SRT 큐 경계가
    어긋나 "영상은 스페인어 타이밍인데 한국어 원문은 다른 타이밍"이 되던
    문제를,애초에 문장 단위로 정렬하지 않음으로써 없앤다.

    어느 큐에도 안 담긴 한국어 단어(내레이션, 화면 밖 발화 등)는 간격
    기준으로 묶어 대상언어 없는 반쪽짜리 AlignedPair로 남긴다(design §9,
    '정렬 실패' finding 처리 대상)."""
    consumed = [False] * len(korean_words)
    pairs: List[AlignedPair] = []

    for j, target in enumerate(target_segments):
        bucket_indices = [
            i for i, w in enumerate(korean_words)
            if not consumed[i] and _midpoint_in(w, target)
        ]
        for i in bucket_indices:
            consumed[i] = True
        bucket = [korean_words[i] for i in bucket_indices]
        korean: Optional[SegmentText] = _merge_words(bucket) if bucket else None
        pairs.append(AlignedPair(id=f"pair_{j+1}", korean=korean, target=target))

    leftover = [w for w, used in zip(korean_words, consumed) if not used]
    for k, group in enumerate(_group_by_gap(leftover)):
        pairs.append(AlignedPair(id=f"pair_korean_{k+1}", korean=_merge_words(group), target=None))

    return pairs
