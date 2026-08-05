"""영어 SRT에서 대명사(he/she) 힌트를 추출해 성별 체크가 걸린 줄에 참고
정보로 붙이는 모듈. design §영어 SRT 대조: "자동으로 성별을 확정하지
않는다" — 이 모듈은 힌트 데이터를 계산만 하고, 저장/적용 여부는 호출자
(pipeline.py)와 사람 리뷰 화면의 몫이다."""

import re
from typing import List, Optional
from app.schemas import SegmentText

_HE_RE = re.compile(r"\b(he|him|his)\b", re.IGNORECASE)
_SHE_RE = re.compile(r"\b(she|her|hers)\b", re.IGNORECASE)


def _overlap(start: float, end: float, seg: SegmentText) -> float:
    inter_start = max(start, seg.start)
    inter_end = min(end, seg.end)
    inter = max(0.0, inter_end - inter_start)
    union = max(end, seg.end) - min(start, seg.start)
    return inter / union if union > 0 else 0.0


def find_pronoun_hint(start: float, end: float,
                       english_segments: List[SegmentText]) -> Optional[dict]:
    """대상 세그먼트(start~end)와 시간대가 가장 많이 겹치는 영어 SRT 세그먼트를
    찾아 그 텍스트의 he/him/his, she/her/hers 개수를 센다. 겹치는 세그먼트가
    없으면 None을 반환한다 — "영어 SRT는 있지만 이 줄에 대응하는 대사가
    없음"과 "대응 대사는 있는데 대명사가 0개"를 구분하기 위함이다."""
    best: Optional[SegmentText] = None
    best_score = 0.0
    for seg in english_segments:
        score = _overlap(start, end, seg)
        if score > best_score:
            best_score, best = score, seg
    if best is None:
        return None
    return {
        "text": best.text,
        "he_count": len(_HE_RE.findall(best.text)),
        "she_count": len(_SHE_RE.findall(best.text)),
    }
