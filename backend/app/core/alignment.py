"""한국어 STT 세그먼트와 대상언어(스페인어) 자막 세그먼트를 타임코드 기준으로 짝짓는 정렬 모듈."""

from typing import List, Optional, Tuple
from app.schemas import SegmentText, AlignedPair


def _overlap(a: SegmentText, b: SegmentText) -> float:
    start = max(a.start, b.start)
    end = min(a.end, b.end)
    inter = max(0.0, end - start)
    union = max(a.end, b.end) - min(a.start, b.start)
    return inter / union if union > 0 else 0.0


def align(korean_segments: List[SegmentText],
          target_segments: List[SegmentText]) -> List[AlignedPair]:
    """타임코드 중첩 비율(IoU)이 가장 높은 한국어-대상언어 세그먼트끼리 짝짓는다.
    겹치는 상대가 없는 세그먼트는 반쪽만 채워진 AlignedPair로 남겨 그 자체로
    '정렬 실패' finding 처리할 수 있게 한다 (design §9)."""
    used_targets = set()
    pairs: List[AlignedPair] = []
    for i, k in enumerate(korean_segments):
        best_idx: Optional[int] = None
        best_score = 0.0
        for j, t in enumerate(target_segments):
            if j in used_targets:
                continue
            score = _overlap(k, t)
            if score > best_score:
                best_score, best_idx = score, j
        if best_idx is not None and best_score > 0.0:
            used_targets.add(best_idx)
            pairs.append(AlignedPair(
                id=f"pair_{i+1}", korean=k, target=target_segments[best_idx],
                alignment_confidence=best_score,
            ))
        else:
            pairs.append(AlignedPair(
                id=f"pair_{i+1}", korean=k, target=None, alignment_confidence=0.0,
            ))
    for j, t in enumerate(target_segments):
        if j not in used_targets:
            pairs.append(AlignedPair(
                id=f"pair_target_{j+1}", korean=None, target=t, alignment_confidence=0.0,
            ))
    return pairs
