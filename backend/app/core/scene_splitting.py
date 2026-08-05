"""STT 세그먼트 간 시간 공백을 기준으로 씬을 분리하는 순수 함수 모듈. LLM을
쓰지 않는다 — 이미 있는 타임코드만으로 계산 가능한 결정론적 규칙이다."""

from typing import List
from app.schemas import AlignedPair


def _start_time(pair: AlignedPair) -> float:
    if pair.target is not None:
        return pair.target.start
    if pair.korean is not None:
        return pair.korean.start
    return 0.0


def _end_time(pair: AlignedPair) -> float:
    if pair.target is not None:
        return pair.target.end
    if pair.korean is not None:
        return pair.korean.end
    return 0.0


def split_into_scenes(pairs: List[AlignedPair], gap_threshold: float = 5.0) -> List[List[AlignedPair]]:
    """pairs를 시작 시간 기준으로 정렬한 뒤, 이전 세그먼트의 끝과 다음
    세그먼트의 시작 사이 공백이 gap_threshold 이상이면 새 씬으로 나눈다.
    align()의 반환값은 완전히 시간순이 아니므로(정렬 안 된 target-only pair가
    뒤에 붙음) 여기서 직접 재정렬한다."""
    if not pairs:
        return []

    sorted_pairs = sorted(pairs, key=_start_time)
    scenes: List[List[AlignedPair]] = [[sorted_pairs[0]]]
    prev_end = _end_time(sorted_pairs[0])

    for pair in sorted_pairs[1:]:
        start = _start_time(pair)
        if start - prev_end >= gap_threshold:
            scenes.append([pair])
        else:
            scenes[-1].append(pair)
        prev_end = max(prev_end, _end_time(pair))

    return scenes
