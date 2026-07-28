from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import FindingRow, Segment
from app.schemas import Finding


def _ns(target_version_id: str, local_id: str) -> str:
    """파이프라인 내부 ID(예: "pair_1", "finding_pair_1_translation")를
    target_version 단위로 네임스페이싱한다.

    alignment.align()은 실행마다 1부터 다시 세는 카운터로 pair.id를 만들기
    때문에("pair_1", "pair_2", ...) 서로 다른 target_version의 결과를 같은 DB에
    저장하면 segments/findings의 전역 PK가 충돌한다. ID 생성 규칙 자체는
    파이프라인 한 번의 실행 안에서만 쓰이므로 그대로 두고, 영속화 시점에만
    prefix를 붙여 전역 유일성을 확보한다."""
    return f"{target_version_id}:{local_id}"


async def save_pipeline_result(session: AsyncSession, target_version_id: str,
                                result: dict) -> None:
    # segments 먼저: findings.segment_id가 segments.id를 참조하는 FK이므로,
    # 네임스페이싱된 pair.id를 Segment.id로 써서 findings가 참조할 수 있게 한다.
    for index, pair in enumerate(result["pairs"]):
        if pair.target is not None:
            start, end = pair.target.start, pair.target.end
        elif pair.korean is not None:
            start, end = pair.korean.start, pair.korean.end
        else:
            start, end = 0.0, 0.0
        session.add(Segment(
            id=_ns(target_version_id, pair.id), target_version_id=target_version_id,
            index=index, start=start, end=end,
            korean_text=pair.korean.text if pair.korean else "",
            target_text=pair.target.text if pair.target else "",
        ))

    # 명시적 flush: segments를 findings보다 먼저 INSERT해야
    # findings.segment_id의 FK 제약이 통과한다 (두 모델 간 relationship()이
    # 없어 세션의 자동 의존성 정렬만으로는 순서가 보장되지 않았다).
    await session.flush()

    for f in result["findings"]:
        session.add(FindingRow(
            id=_ns(target_version_id, f.id), target_version_id=target_version_id,
            segment_id=_ns(target_version_id, f.segment_id),
            category=f.category, description=f.description,
            original_text=f.original_text, suggested_text=f.suggested_text,
            confidence=f.confidence, source=f.source, status=f.status,
        ))


async def get_findings(session: AsyncSession, target_version_id: str) -> List[FindingRow]:
    rows = await session.execute(
        select(FindingRow).where(FindingRow.target_version_id == target_version_id)
    )
    return list(rows.scalars().all())
