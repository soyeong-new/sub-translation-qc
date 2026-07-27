from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import FindingRow, Segment
from app.schemas import Finding


async def save_pipeline_result(session: AsyncSession, target_version_id: str,
                                result: dict) -> None:
    # segments 먼저: findings.segment_id가 segments.id를 참조하는 FK이므로,
    # pair.id(예: "pair_1")를 Segment.id로 그대로 써서 findings가 참조할 수 있게 한다.
    for index, pair in enumerate(result["pairs"]):
        if pair.target is not None:
            start, end = pair.target.start, pair.target.end
        elif pair.korean is not None:
            start, end = pair.korean.start, pair.korean.end
        else:
            start, end = 0.0, 0.0
        session.add(Segment(
            id=pair.id, target_version_id=target_version_id, index=index,
            start=start, end=end,
            korean_text=pair.korean.text if pair.korean else "",
            target_text=pair.target.text if pair.target else "",
        ))

    # 명시적 flush: segments를 findings보다 먼저 INSERT해야
    # findings.segment_id의 FK 제약이 통과한다 (두 모델 간 relationship()이
    # 없어 세션의 자동 의존성 정렬만으로는 순서가 보장되지 않았다).
    await session.flush()

    for f in result["findings"]:
        session.add(FindingRow(
            id=f.id, target_version_id=target_version_id, segment_id=f.segment_id,
            category=f.category, description=f.description,
            original_text=f.original_text, suggested_text=f.suggested_text,
            confidence=f.confidence, source=f.source, status=f.status,
        ))


async def get_findings(session: AsyncSession, target_version_id: str) -> List[FindingRow]:
    rows = await session.execute(
        select(FindingRow).where(FindingRow.target_version_id == target_version_id)
    )
    return list(rows.scalars().all())
