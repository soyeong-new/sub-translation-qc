import pytest
from app.main import _fail_stuck_in_progress_target_versions
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _make_target_version(status: str) -> str:
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM", status=status)
        session.add(tv)
        await session.commit()
        return tv.id


@pytest.mark.asyncio
async def test_fails_target_version_stuck_analyzing():
    """서버 재시작으로 백그라운드 태스크가 죽으면 "analyzing"에 영원히
    멈추므로, 시작 시 "failed"로 되돌려 프론트 폴링이 끝나게 해야 한다."""
    tv_id = await _make_target_version("analyzing")
    await _fail_stuck_in_progress_target_versions()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message


@pytest.mark.asyncio
async def test_fails_target_version_stuck_verifying():
    tv_id = await _make_target_version("verifying")
    await _fail_stuck_in_progress_target_versions()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"


@pytest.mark.asyncio
async def test_leaves_finished_target_versions_untouched():
    """이미 끝난 상태(review/awaiting_confirmation/failed)는 건드리면 안
    된다 — 검수자가 보던 결과가 재시작 한 번에 사라지면 안 되므로."""
    review_id = await _make_target_version("review")
    confirm_id = await _make_target_version("awaiting_confirmation")
    await _fail_stuck_in_progress_target_versions()
    async with async_session() as session:
        assert (await session.get(TargetVersion, review_id)).status == "review"
        assert (await session.get(TargetVersion, confirm_id)).status == "awaiting_confirmation"
