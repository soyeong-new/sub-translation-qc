"""한국어 STT 단어와 대상언어(스페인어) 자막 세그먼트를 타임코드 기준으로 짝짓는 정렬 모듈."""

from bisect import bisect_right
from typing import List, Optional, Tuple
from app.schemas import SegmentText, AlignedPair
from app.core.time_overlap import coverage_ratio

# 자막 큐로 안 흡수된 한국어 단어들(내레이션, 화면 밖 발화 등)을 문장처럼
# 묶기 위한 간격 기준 — 이 이하 간격이면 같은 발화의 연속으로 보고 하나로
# 합친다. ponytail: 고정값. 단어 사이 자연스러운 쉼(0.x초)보다는 크고, 서로
# 다른 발화 사이 침묵(1초 이상)보다는 작게 잡음 — 오탐이 잦아지면 조정.
ORPHAN_WORD_GAP_SECONDS = 1.0

# align_by_korean_cue에서 두 큐가 "진짜 겹친다"고 볼 최소 겹침 시간(초).
# ponytail: 고정값. 타이밍 오차로 경계가 수십 밀리초 스치는 정도(예:
# 0.02초)는 무관한 인접 큐를 잘못 묶는 원인이 될 수 있어, 그보다는 크게
# 잡는다. 실제 발화 겹침(동시에 여러 명이 말하는 등)은 보통 이보다 훨씬
# 길다.
MIN_CUE_OVERLAP_SECONDS = 0.05

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


def _best_overlapping_target(word: SegmentText, target_segments: List[SegmentText]) -> Optional[int]:
    """word 시간의 몇 %가 각 target_segments 안에 들어가는지(coverage_ratio)로
    가장 많이 겹치는 인덱스를 반환한다 — 한국어/영어/스페인어 SRT는
    제작처가 각자 타이밍을 잡아 미세하게 어긋나는 경우가 흔해서(design
    2026-08-11-korean-srt-input-design.md 후속 논의), "중점이 구간 안에
    있는가"(점 판정)만으로는 짧은 반응 대사처럼 자막 표시 구간이 실제
    발화보다 긴 경우 인접 구간으로 새거나 아예 못 붙는다.

    IoU(교집합/합집합)가 아니라 word 기준 커버리지를 쓰는 이유: 단어가
    "쿼리"고 큐가 "후보"인 비대칭 매칭이라, 후보 큐가 짧을수록 유리해지는
    IoU의 왜곡이 없다(실제 사례: 5초짜리 큐와 1초짜리 큐에 단어 하나가
    거의 반반씩 걸쳐 있는데도 IoU로는 1초짜리가 압도적으로 이겨버렸음).

    겹치는 구간이 없으면(내레이션, 화면 밖 발화 등) None."""
    best_index: Optional[int] = None
    best_score = 0.0
    for i, target in enumerate(target_segments):
        score = coverage_ratio(word.start, word.end, target.start, target.end)
        if score > best_score:
            best_score, best_index = score, i
    return best_index


def _merge_words(words: List[SegmentText]) -> SegmentText:
    return SegmentText(
        start=words[0].start, end=max(w.end for w in words),
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
    글자수 제약도 이 큐 단위로 움직이기 때문이다. 각 한국어 단어를 가장
    많이 겹치는(IoU 기준, _best_overlapping_target) 큐에 배정해 그 큐의
    한국어 텍스트로 삼는다(문장 대 문장 정렬이 아니라 단어를 SRT 큐에
    담는 방식) — 위스퍼가 침묵 기준으로 끊는 문장 경계와 SRT 큐 경계가
    어긋나 "영상은 스페인어 타이밍인데 한국어 원문은 다른 타이밍"이 되던
    문제를, 애초에 문장 단위로 정렬하지 않음으로써 없앤다.

    "중점이 구간 안에 있는가"가 아니라 "구간이 얼마나 겹치는가"로 판단하는
    이유: 한국어 SRT 단어를 STT가 못 들어 큐 경계로 폴백할 때
    (stt_srt_matching의 cue-bound 폴백 경로) 큐 하나에 단어가 하나뿐이면
    그 단어의 타임코드가 큐 표시 구간 전체로 늘어나는데, 표시 구간은 보통
    실제 발화보다 길어서 중점이 대응하는 대상언어 큐 밖으로 새는 경우가
    실제로 있었다(design 2026-08-11-korean-srt-input-design.md 후속 논의)
    — 겹침 기준이면 이런 경우도 정상적으로 붙는다.

    어느 큐와도 안 겹치는 한국어 단어(내레이션, 화면 밖 발화 등)는 간격
    기준으로 묶어 대상언어 없는 반쪽짜리 AlignedPair로 남긴다(design §9,
    '정렬 실패' finding 처리 대상)."""
    buckets: List[List[SegmentText]] = [[] for _ in target_segments]
    leftover: List[SegmentText] = []

    for word in korean_words:
        best_index = _best_overlapping_target(word, target_segments)
        if best_index is None:
            leftover.append(word)
        else:
            buckets[best_index].append(word)

    pairs: List[AlignedPair] = []
    for j, target in enumerate(target_segments):
        korean: Optional[SegmentText] = _merge_words(buckets[j]) if buckets[j] else None
        pairs.append(AlignedPair(id=f"pair_{j+1}", korean=korean, target=target))

    for k, group in enumerate(_group_by_gap(leftover)):
        pairs.append(AlignedPair(id=f"pair_korean_{k+1}", korean=_merge_words(group), target=None))

    return pairs


def _cues_overlap(a: SegmentText, b: SegmentText) -> bool:
    return min(a.end, b.end) - max(a.start, b.start) > MIN_CUE_OVERLAP_SECONDS


def align_by_korean_cue(korean_cues: List[SegmentText],
                         target_segments: List[SegmentText]) -> List[AlignedPair]:
    """한국어 SRT가 있을 때 쓰는 정렬 함수(design 2026-08-13-korean-srt-
    cue-based-segmentation-design.md). 한국어 SRT 큐와 대상언어 SRT 큐를
    시간 겹침 기준으로 서로 묶는다(Union-Find로 연결 요소를 찾는다) —
    한쪽이 다른 쪽 여러 개와 겹치면 전부 하나의 그룹으로 합쳐 텍스트를
    이어붙인다. 이렇게 하면 "원래 한국어에서 한 문장이었던 게 대상언어
    큐 경계 때문에 잘리는" 문제(한국어 큐 1개 ↔ 대상언어 큐 여러 개)와
    "대상언어 한 줄이 여러 한국어 큐에 걸쳐 같은 텍스트가 중복되는" 문제
    (한국어 큐 여러 개 ↔ 대상언어 큐 1개)를 하나의 규칙으로 없앤다.

    korean_srt_path가 없는 경로는 이 함수를 쓰지 않는다 — "한국어 큐"라는
    단위 자체가 없어(STT가 잡는 문장 경계는 침묵 기준일 뿐 신뢰할 수
    없음) 기존 align()(단어를 대상언어 큐에 담는 방식)을 그대로 쓴다.

    겹치는 짝이 없는 큐(내레이션, 화면 밖 대사, 대응 원문을 못 찾은
    번역 줄 등)는 반쪽짜리 AlignedPair로 남는다 — 검수 화면에서 검수자가
    "제외" 표시를 할 수 있다(design §신규: 제외 표시)."""
    n_k, n_t = len(korean_cues), len(target_segments)
    parent = list(range(n_k + n_t))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for ki, k in enumerate(korean_cues):
        for ti, t in enumerate(target_segments):
            if _cues_overlap(k, t):
                union(ki, n_k + ti)

    groups: dict = {}
    for ki in range(n_k):
        groups.setdefault(find(ki), {"korean": [], "target": []})["korean"].append(ki)
    for ti in range(n_t):
        groups.setdefault(find(n_k + ti), {"korean": [], "target": []})["target"].append(ti)

    def group_start(group: dict) -> float:
        starts = [korean_cues[ki].start for ki in group["korean"]]
        starts += [target_segments[ti].start for ti in group["target"]]
        return min(starts)

    pairs: List[AlignedPair] = []
    for i, group in enumerate(sorted(groups.values(), key=group_start)):
        korean = _merge_words(
            [korean_cues[ki] for ki in sorted(group["korean"], key=lambda ki: korean_cues[ki].start)]
        ) if group["korean"] else None
        target = _merge_words(
            [target_segments[ti] for ti in sorted(group["target"], key=lambda ti: target_segments[ti].start)]
        ) if group["target"] else None
        pairs.append(AlignedPair(id=f"pair_{i+1}", korean=korean, target=target))
    return pairs
