from typing import List
from app.core.ingest import build_srt
from app.core.format_rules import check_line_length
from app.schemas import AlignedPair, SegmentText, ExportStats


def _final_text_by_segment(findings: List[dict]) -> dict:
    """세그먼트별 최종 텍스트 맵.

    한 세그먼트에 여러 finding이 걸릴 수 있다 (예: 자동보정된 온점 위반과
    검수자가 직접 고친 줄 길이 위반이 같은 세그먼트를 가리키는 경우). 지켜야 할
    불변식은 "검수자의 명시적 판단은 자동 적용된 기계적 보정을 항상 이긴다"이다.

    그 신호로 reviewed_at을 쓴다: review-action 엔드포인트만 이 값을 채우고,
    save_pipeline_result가 만드는 자동보정 finding은 NULL로 남는다. source는
    finding을 '누가 만들었는지'일 뿐 '누가 해결했는지'가 아니라서 이 판단에
    쓸 수 없다 — 검수자가 규칙 기반 finding을 modified로 고쳐도 source는
    "rule"로 남기 때문이다.

    검수되지 않은 것 먼저, 검수된 것 나중에 적용해 뒤에 오는 쪽이 이기게 한다.
    이 정렬이 없으면 결과가 DB의 행 반환 순서에 좌우된다."""
    reflected = [
        f for f in findings
        if f["status"] in ("approved", "modified") and f["final_text"]
    ]
    reflected.sort(key=lambda f: f.get("reviewed_at") is not None)
    return {f["segment_id"]: f["final_text"] for f in reflected}


def assemble_final_srt(segments: List[dict], findings: List[dict]) -> str:
    """최종 SRT를 조립한다.

    두 가지를 보정한다:
    1. 대상언어 텍스트가 빈 세그먼트(정렬되지 않은 한국어 전용 세그먼트)는
       건너뛴다 — 빈 큐는 정보 가치가 없고 대부분의 SRT 플레이어가 깨진 자막으로
       취급한다.
    2. 저장 순서(index)가 아니라 실제 타임코드(start) 순으로 정렬한다 —
       alignment.align()이 짝을 못 찾은 대상언어 세그먼트를 뒤에 몰아 붙이기
       때문에 index 순서는 시간 순서와 일치하지 않는다.
    """
    final_by_segment = _final_text_by_segment(findings)
    entries = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        text = final_by_segment.get(seg["id"], seg["text"])
        if not text.strip():
            continue
        entries.append({"start": seg["start"], "end": seg["end"], "text": text})
    return build_srt(entries)


def safety_net_check(segments: List[dict], findings: List[dict]) -> list:
    """export 직전 안전망 (design §5-1의 3번 지점). 검수자의 직접 수정 텍스트
    까지 포함한 최종 텍스트를 대상으로 줄 길이 규칙을 마지막으로 한 번 더
    검사한다."""
    final_by_segment = _final_text_by_segment(findings)
    pairs = [
        AlignedPair(id=seg["id"], target=SegmentText(
            start=seg["start"], end=seg["end"],
            text=final_by_segment.get(seg["id"], seg["text"]),
        ))
        for seg in segments
    ]
    return check_line_length(pairs)


def compute_stats(findings: List[dict]) -> ExportStats:
    total = len(findings)
    reflected = sum(1 for f in findings if f["status"] in ("approved", "modified"))
    return ExportStats(
        finding_count=total,
        reflection_rate=(reflected / total) if total else 0.0,
    )
