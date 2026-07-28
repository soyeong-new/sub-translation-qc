from datetime import datetime

import pytest
from sqlalchemy import select
from app.db import async_session, engine
from app.models import Base, Title, Episode, TargetVersion, Segment
from app.repositories import save_pipeline_result, get_findings
from app.schemas import Finding, AlignedPair, SegmentText


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # pytest-asyncio는 기본적으로 테스트마다 새 이벤트 루프를 쓰는데, engine의 asyncpg
    # 커넥션 풀은 모듈 임포트 시점에 한 번만 만들어져 특정 루프에 바인딩된다. dispose하지
    # 않으면 다음 테스트(다른 루프)가 이 풀의 커넥션을 재사용하려다
    # "attached to a different loop" RuntimeError로 죽는다.
    await engine.dispose()


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_findings():
    async with async_session() as session:
        # NOTE: Title.created_at defaults to a tz-aware datetime (Task 13's
        # models.py), but the DB column is TIMESTAMP WITHOUT TIME ZONE — asyncpg
        # rejects tz-aware values for that column type. Passing a naive value
        # explicitly here sidesteps a pre-existing bug unrelated to this task;
        # see task-14-report.md for details.
        title = Title(name="Test Movie", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()

        result = {
            "findings": [Finding(
                id="f1", target_version_id=tv.id, segment_id="p1",
                category="translation", description="근거", original_text="a",
                suggested_text="b", confidence=0.9, source="llm",
            )],
            "format_violations": [], "characters": [], "relationships": [],
            "gender_questions": [], "register_questions": [],
            "pairs": [AlignedPair(
                id="p1",
                korean=SegmentText(start=0.0, end=1.5, text="한국어"),
                target=SegmentText(start=0.0, end=1.5, text="target text"),
            )],
        }
        await save_pipeline_result(session, tv.id, result)
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 1
        assert rows[0].category == "translation"

        seg_rows = await session.execute(select(Segment).where(Segment.target_version_id == tv.id))
        segments = list(seg_rows.scalars().all())
        assert len(segments) == 1
        assert segments[0].id == "p1"
        assert segments[0].korean_text == "한국어"
        assert segments[0].target_text == "target text"
