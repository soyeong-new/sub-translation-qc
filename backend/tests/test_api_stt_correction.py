import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import Base, TargetVersion, Episode, Title, Segment, SttCorrection


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_correct_stt_updates_segment_and_records_correction(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안뇽하세요", target_text="Hola")
        session.add(seg); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "안녕하세요", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        assert r.json()["korean_text"] == "안녕하세요"

    async with async_session() as session:
        rows = await session.execute(
            select(SttCorrection).where(SttCorrection.segment_id == seg_id)
        )
        corrections = list(rows.scalars().all())
        assert len(corrections) == 1
        assert corrections[0].original_text == "안뇽하세요"
        assert corrections[0].corrected_text == "안녕하세요"
        assert corrections[0].reviewer_name == "김검수"
