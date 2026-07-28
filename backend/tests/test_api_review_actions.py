import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, FindingRow, Title, Episode, TargetVersion, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _make_finding_row(finding_id: str) -> None:
    # NOTE: Task 13 added FK constraints (findings.target_version_id ->
    # target_versions.id, findings.segment_id -> segments.id) enforced by the
    # real Postgres DB. The brief's literal test used bare "tv1"/"p1" strings,
    # which violate those FKs on insert. Following the precedent set in Task 14
    # (see task-14-report.md), real parent rows are created first so the
    # FindingRow insert succeeds; the finding's own id stays the literal "f1"
    # since the review-action tests target it by path parameter.
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        segment = Segment(id="p1", target_version_id=tv.id, index=0, start=0.0, end=1.0)
        session.add(segment)
        await session.flush()
        f = FindingRow(id=finding_id, target_version_id=tv.id, segment_id="p1",
                       category="translation", description="근거",
                       original_text="a", suggested_text="b", confidence=0.9)
        session.add(f)
        await session.commit()


@pytest.mark.asyncio
async def test_reviewer_can_approve_a_finding():
    await _make_finding_row("f1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reviewer_can_modify_with_final_text():
    await _make_finding_row("f1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "modified", "reviewer_name": "김검수", "final_text": "c"},
        )
        assert r.status_code == 200
        assert r.json()["final_text"] == "c"
