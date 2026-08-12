"""두 시간 구간이 얼마나 겹치는지 계산하는 공용 유틸리티.

한국어/영어/스페인어 SRT는 제작처가 각자 따로 타이밍을 잡아서, 미세한
초 단위 어긋남이 흔하다(design 2026-08-11-korean-srt-input-design.md
후속 논의). "이 구간이 어느 후보 구간에 대응하는가"를 판단할 때, 쿼리
구간(단어 하나, 또는 참조용 세그먼트 하나) 기준으로 "내 시간의 몇 %가
그 후보 안에 들어가는가"(커버리지)로 판단해야 한다 — IoU(교집합/합집합)를
쓰면 후보 구간이 짧을수록 합집합이 작아져 유리해지는 왜곡이 생긴다(실제
사례: 5초짜리 후보와 1초짜리 후보에 단어 하나가 거의 반반씩 걸쳐 있는데도
IoU로는 1초짜리가 압도적으로 이겨버림 — 진짜는 애매한 경우인데 확신하는
걸로 잘못 나옴). alignment.align()의 한국어-대상언어 단어 매칭이 이
기준을 쓴다."""


def coverage_ratio(query_start: float, query_end: float,
                    candidate_start: float, candidate_end: float) -> float:
    """query 구간 시간의 몇 %가 candidate 구간 안에 들어가는지(0.0~1.0)."""
    inter_start = max(query_start, candidate_start)
    inter_end = min(query_end, candidate_end)
    inter = max(0.0, inter_end - inter_start)
    query_duration = query_end - query_start
    return inter / query_duration if query_duration > 0 else 0.0
