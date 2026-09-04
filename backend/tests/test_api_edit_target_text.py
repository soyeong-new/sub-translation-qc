import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, TargetVersion, Episode, Title, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_segment(target_text="Hola"):
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안녕하세요", target_text=target_text)
        session.add(seg); await session.commit()
        return seg.id


@pytest.mark.asyncio
async def test_edit_target_text_updates_segment_without_history():
    seg_id = await _make_segment()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": "Hola, ¿cómo estás?"},
        )
        assert r.status_code == 200
        assert r.json()["target_text"] == "Hola, ¿cómo estás?"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.target_text == "Hola, ¿cómo estás?"


@pytest.mark.asyncio
async def test_edit_target_text_rejects_line_over_length_limit():
    seg_id = await _make_segment()
    transport = ASGITransport(app=app)
    too_long = "a" * 51
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": too_long},
        )
        assert r.status_code == 400

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.target_text == "Hola"
