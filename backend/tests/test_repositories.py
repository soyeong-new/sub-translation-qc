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
        # 영속화된 segment id는 target_version_id로 네임스페이싱된다.
        assert segments[0].id == f"{tv.id}:p1"
        assert segments[0].korean_text == "한국어"
        assert segments[0].target_text == "target text"
        # findings.segment_id도 같은 네임스페이싱된 id를 가리켜야 FK가 성립한다.
        assert rows[0].segment_id == f"{tv.id}:p1"
        assert rows[0].id == f"{tv.id}:f1"


def _pipeline_result(target_version_id: str) -> dict:
    """alignment.align()이 실행마다 재사용하는 로컬 ID("pair_1")를 그대로 쓴
    파이프라인 결과. 두 target_version에서 동일한 로컬 ID가 나온다."""
    return {
        "findings": [Finding(
            id="finding_pair_1_translation", target_version_id=target_version_id,
            segment_id="pair_1", category="translation", description="근거",
            original_text="a", suggested_text="b", confidence=0.9, source="llm",
        )],
        "format_violations": [], "characters": [], "relationships": [],
        "gender_questions": [], "register_questions": [],
        "pairs": [AlignedPair(
            id="pair_1",
            korean=SegmentText(start=0.0, end=1.5, text="한국어"),
            target=SegmentText(start=0.0, end=1.5, text="target text"),
        )],
    }


@pytest.mark.asyncio
async def test_two_target_versions_can_be_saved_without_pk_collision():
    """회귀 테스트: alignment의 pair.id는 실행마다 "pair_1"부터 다시 시작하므로,
    서로 다른 target_version의 결과를 같은 DB에 저장하면 segments/findings의
    전역 PK가 충돌했다 (IntegrityError: duplicate key ... "segments_pkey")."""
    async with async_session() as session:
        title_a = Title(name="Movie A", type="movie", created_at=datetime.now())
        title_b = Title(name="Movie B", type="movie", created_at=datetime.now())
        session.add_all([title_a, title_b])
        await session.flush()
        ep_a = Episode(title_id=title_a.id, video_path="/a.mp4")
        ep_b = Episode(title_id=title_b.id, video_path="/b.mp4")
        session.add_all([ep_a, ep_b])
        await session.flush()
        tv_a = TargetVersion(episode_id=ep_a.id, target_language="es", variant="LATAM")
        tv_b = TargetVersion(episode_id=ep_b.id, target_language="es", variant="LATAM")
        session.add_all([tv_a, tv_b])
        await session.flush()

        await save_pipeline_result(session, tv_a.id, _pipeline_result(tv_a.id))
        await session.commit()
        # 두 번째 저장이 첫 번째와 충돌하지 않아야 한다.
        await save_pipeline_result(session, tv_b.id, _pipeline_result(tv_b.id))
        await session.commit()

        all_segments = list((await session.execute(select(Segment))).scalars().all())
        assert len(all_segments) == 2
        assert {s.id for s in all_segments} == {f"{tv_a.id}:pair_1", f"{tv_b.id}:pair_1"}

        rows_a = await get_findings(session, tv_a.id)
        rows_b = await get_findings(session, tv_b.id)
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].segment_id == f"{tv_a.id}:pair_1"
        assert rows_b[0].segment_id == f"{tv_b.id}:pair_1"
