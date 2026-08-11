import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title


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
async def test_create_episode_persists_english_srt_path(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.validation.MEDIA_ROOT", tmp_path)
    srt_en_dir = tmp_path / "srt_en"
    srt_en_dir.mkdir(parents=True)
    srt_file = srt_en_dir / "x.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "english_srt_path": str(srt_file)},
        )
    assert episode_res.status_code == 200
    from app.models import Episode
    async with async_session() as session:
        episode = await session.get(Episode, episode_res.json()["id"])
        assert episode.english_srt_path == str(srt_file)


@pytest.mark.asyncio
async def test_create_episode_persists_korean_srt_path(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.validation.MEDIA_ROOT", tmp_path)
    srt_ko_dir = tmp_path / "srt_ko"
    srt_ko_dir.mkdir(parents=True)
    srt_file = srt_ko_dir / "ko.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n", encoding="utf-8")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "korean_srt_path": str(srt_file)},
        )
    assert episode_res.status_code == 200
    from app.models import Episode
    async with async_session() as session:
        episode = await session.get(Episode, episode_res.json()["id"])
        assert episode.korean_srt_path == str(srt_file)


@pytest.mark.asyncio
async def test_create_episode_rejects_korean_srt_path_outside_srt_ko_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.validation.MEDIA_ROOT", tmp_path)
    (tmp_path / "srt_ko").mkdir(parents=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        r = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "korean_srt_path": "/etc/passwd"},
        )
        assert r.status_code == 400

    from app.models import Episode
    async with async_session() as session:
        result = await session.execute(select(Episode).where(Episode.title_id == title_id))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_create_episode_rejects_english_srt_path_outside_srt_en_dir(monkeypatch, tmp_path):
    """Finding #1 회귀: english_srt_path는 클라이언트가 넘긴 임의 경로다.
    pipeline.load_srt()가 이 경로를 열어 파싱한 텍스트가
    Segment.english_pronoun_hint로 저장되어 flagged-segments 응답을 통해
    그대로 노출되므로, MEDIA_ROOT/srt_en 밖을 가리키는 경로(절대 경로든
    ".." 트래버설이든)는 400으로 거부해야 한다."""
    monkeypatch.setattr("app.core.validation.MEDIA_ROOT", tmp_path)
    srt_en_dir = tmp_path / "srt_en"
    srt_en_dir.mkdir(parents=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]

        # 완전히 밖에 있는 절대 경로
        r = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "english_srt_path": "/etc/passwd"},
        )
        assert r.status_code == 400

        # 문자열상으로는 srt_en/ 아래처럼 보이지만 ".."으로 실제로는 밖을
        # 가리키는 경로 — resolve() 없이 lexical하게만 검사하면 통과해버린다.
        traversal_path = srt_en_dir / ".." / ".." / "etc" / "passwd"
        r = await client.post(
            f"/titles/{title_id}/episodes",
            json={"video_path": "/x.mp4", "english_srt_path": str(traversal_path)},
        )
        assert r.status_code == 400

    from app.models import Episode
    async with async_session() as session:
        result = await session.execute(select(Episode).where(Episode.title_id == title_id))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_list_titles_returns_created_titles():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/titles", json={"name": "T1", "type": "movie"})
        await client.post("/titles", json={"name": "T2", "type": "series"})
        r = await client.get("/titles")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert names == {"T1", "T2"}



@pytest.mark.asyncio
async def test_list_titles_includes_nested_episodes_and_target_versions():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "movie"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes", json={"video_path": "/x.mp4"})
        episode_id = episode_res.json()["id"]
        tv_res = await client.post(
            f"/episodes/{episode_id}/target-versions",
            json={"target_language": "es", "variant": "LATAM"})
        tv_id = tv_res.json()["id"]

        r = await client.get("/titles")
    title = next(t for t in r.json() if t["id"] == title_id)
    assert len(title["episodes"]) == 1
    assert title["episodes"][0]["id"] == episode_id
    tvs = title["episodes"][0]["target_versions"]
    assert len(tvs) == 1
    assert tvs[0]["id"] == tv_id
    assert tvs[0]["status"] == "analyzing"
    assert tvs[0]["target_language"] == "es"


@pytest.mark.asyncio
async def test_delete_title_removes_title_and_children(tmp_path):
    from app.models import Episode, TargetVersion, Segment, FindingRow

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "movie"})
        title_id = title_res.json()["id"]
        episode_res = await client.post(
            f"/titles/{title_id}/episodes", json={"video_path": str(video_path)})
        episode_id = episode_res.json()["id"]
        tv_res = await client.post(
            f"/episodes/{episode_id}/target-versions",
            json={"target_language": "es", "variant": "LATAM"})
        tv_id = tv_res.json()["id"]

    async with async_session() as session:
        session.add(Segment(id="seg1", target_version_id=tv_id, index=0, start=0.0, end=1.0))
        await session.flush()
        session.add(FindingRow(
            id="f1", target_version_id=tv_id, segment_id="seg1", category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0))
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete(f"/titles/{title_id}")
    assert r.status_code == 200

    assert not video_path.exists()
    async with async_session() as session:
        assert await session.get(Title, title_id) is None
        assert await session.get(Episode, episode_id) is None
        assert await session.get(TargetVersion, tv_id) is None
        assert await session.get(Segment, "seg1") is None
        assert await session.get(FindingRow, "f1") is None


@pytest.mark.asyncio
async def test_delete_title_returns_404_for_missing_title():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete("/titles/does-not-exist")
    assert r.status_code == 404


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
