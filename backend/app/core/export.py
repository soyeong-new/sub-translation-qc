from typing import List
from app.core.ingest import build_srt
from app.core.format_rules import check_line_length
from app.schemas import AlignedPair, SegmentText, ExportStats


def assemble_final_srt(segments: List[dict], findings: List[dict]) -> str:
    final_by_segment = {
        f["segment_id"]: f["final_text"]
        for f in findings if f["status"] in ("approved", "modified") and f["final_text"]
    }
    entries = []
    for seg in segments:
        text = final_by_segment.get(seg["id"], seg["text"])
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
