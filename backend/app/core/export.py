from typing import List
from app.core.ingest import build_srt
from app.core.format_rules import check_line_length
from app.schemas import AlignedPair, SegmentText, ExportStats


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
    final_by_segment = {
        f["segment_id"]: f["final_text"]
        for f in findings if f["status"] in ("approved", "modified") and f["final_text"]
    }
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
    final_by_segment = {
        f["segment_id"]: f["final_text"]
        for f in findings if f["status"] in ("approved", "modified") and f["final_text"]
    }
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
