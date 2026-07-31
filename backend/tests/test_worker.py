from datetime import datetime
from unittest.mock import patch
import pytest
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion
from app.worker import run_analysis_job

TARGET_SRT = """1
00:00:00,000 --> 00:00:02,000
hola
"""


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_target_version(status="analyzing") -> str:
    async with async_session() as session:
        title = Title(name="T", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status=status)
        session.add(tv)
        await session.commit()
        return tv.id


@pytest.mark.asyncio
async def test_job_sets_status_review_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"):
        await run_analysis_job({}, tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "review"
        assert tv.error_message is None


@pytest.mark.asyncio
async def test_job_sets_status_failed_on_exception(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    with patch("app.worker.run_pipeline", side_effect=RuntimeError("STT 실패")):
        await run_analysis_job({}, tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message == "STT 실패"
