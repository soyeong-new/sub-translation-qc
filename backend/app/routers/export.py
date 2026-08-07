"""검수 완료된 내용을 최종 SRT로 조립해 내보내는 엔드포인트."""

from pathlib import Path
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, FindingRow, Segment, ExportRow
from app.core.export import assemble_final_srt, compute_stats, safety_net_check

router = APIRouter()


@router.get("/target-versions/{target_version_id}/export")
async def export_target_version(target_version_id: str):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        # export는 저장 순서(index)가 아니라 타임코드 순으로 내보낸다 —
        # alignment.align()이 짝을 못 찾은 대상언어 세그먼트를 목록 뒤에 붙이므로
        # index 순서는 실제 재생 순서와 다를 수 있다.
        seg_rows = (await session.execute(
            select(Segment).where(Segment.target_version_id == target_version_id)
            .order_by(Segment.start)
        )).scalars().all()
        finding_rows = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == target_version_id)
        )).scalars().all()

    segments = [{"id": s.id, "start": s.start, "end": s.end, "text": s.target_text} for s in seg_rows]
    # reviewed_at도 함께 넘긴다: 같은 세그먼트에 자동보정과 검수자 판단이 동시에
    # 걸린 경우 어느 쪽이 최종 텍스트가 되는지 결정하는 데 쓰인다 (검수자 우선).
    findings = [{"segment_id": f.segment_id, "status": f.status,
                 "final_text": f.final_text, "reviewed_at": f.reviewed_at}
                for f in finding_rows]
    srt = assemble_final_srt(segments, findings)
    stats = compute_stats(findings)
    # 안전망 (design §5-1의 3번 지점): assemble_final_srt와 동일한 최종 텍스트를
    # 대상으로 줄 길이를 마지막으로 한 번 더 검사한다. 위반이 있어도 export
    # 자체는 막지 않고 참고용 경고로만 응답에 포함한다 (non-blocking).
    warnings = safety_net_check(segments, findings)

    # export 이력/감사 기록 (exports 테이블). 응답으로 내려준 통계와 정확히 같은
    # 값을 남긴다.
    async with async_session() as session:
        session.add(ExportRow(
            target_version_id=target_version_id,
            finding_count=stats.finding_count,
            reflection_rate=stats.reflection_rate,
        ))
        tv = await session.get(TargetVersion, target_version_id)
        if tv is not None and tv.video_proxy_path:
            Path(tv.video_proxy_path).unlink(missing_ok=True)
            tv.video_proxy_path = None
        await session.commit()

    return {
        "srt": srt,
        "stats": stats.model_dump(),
        "format_warnings": [w.model_dump() for w in warnings],
    }
