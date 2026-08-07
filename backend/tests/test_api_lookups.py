import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_list_segments_for_target_version():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        session.add(Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                            korean_text="안녕", target_text="Hola"))
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/segments")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["korean_text"] == "안녕"


@pytest.mark.asyncio
async def test_get_target_version_returns_status_and_error_message():
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="failed", error_message="Gemini API 오류: timeout")
        session.add(tv)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Gemini API 오류: timeout"


@pytest.mark.asyncio
async def test_get_target_version_404_when_missing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/nonexistent")
    assert r.status_code == 404
