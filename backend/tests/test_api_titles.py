import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # pytest-asyncio는 테스트마다 새 이벤트 루프를 쓰는데, engine의 asyncpg 커넥션
    # 풀은 모듈 임포트 시점에 한 번만 만들어져 특정 루프에 바인딩된다. dispose하지
    # 않으면 다음 테스트(다른 루프)가 이 풀의 커넥션을 재사용하려다
    # "attached to a different loop" RuntimeError로 죽는다. (test_repositories.py와 동일 패턴)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_title_episode_and_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/titles", json={"name": "The Peach Tree", "type": "movie"})
        assert r.status_code == 200
        title_id = r.json()["id"]

        r = await client.post(f"/titles/{title_id}/episodes",
                               json={"episode_no": None, "video_path": "/videos/x.mp4"})
        assert r.status_code == 200
        episode_id = r.json()["id"]

        r = await client.post(f"/episodes/{episode_id}/target-versions",
                               json={"target_language": "es", "variant": "LATAM"})
        assert r.status_code == 200
        assert r.json()["status"] == "analyzing"


@pytest.mark.asyncio
async def test_create_episode_persists_english_srt_path():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "english_srt_path": "/media/srt_en/x.srt"},
        )
    assert episode_res.status_code == 200
    from app.models import Episode
    async with async_session() as session:
        episode = await session.get(Episode, episode_res.json()["id"])
        assert episode.english_srt_path == "/media/srt_en/x.srt"


@pytest.mark.asyncio
async def test_create_episode_english_srt_path_defaults_to_none():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes", json={"video_path": "/x.mp4"},
        )
    assert episode_res.status_code == 200
    from app.models import Episode
    async with async_session() as session:
        episode = await session.get(Episode, episode_res.json()["id"])
        assert episode.english_srt_path is None
