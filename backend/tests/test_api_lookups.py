import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, Character, Relationship, Segment


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
async def test_list_characters_for_target_version():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        session.add(Character(title_id=title.id, label="테스트인물"))
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/characters")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["label"] == "테스트인물"
        assert body[0]["confirmed_gender"] is None


@pytest.mark.asyncio
async def test_list_characters_scoped_by_title_shared_across_episodes():
    # Characters are keyed by title_id, not target_version_id (design §: 인물은 작품
    # 단위 전역 공유). A character created against one episode of a title must still
    # show up when listed via a *different* episode/target-version of the same title.
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        ep1 = Episode(title_id=title.id, video_path="/1.mp4")
        ep2 = Episode(title_id=title.id, video_path="/2.mp4")
        session.add_all([ep1, ep2]); await session.flush()
        tv1 = TargetVersion(episode_id=ep1.id, target_language="es", variant="LATAM")
        tv2 = TargetVersion(episode_id=ep2.id, target_language="es", variant="LATAM")
        session.add_all([tv1, tv2]); await session.flush()
        session.add(Character(title_id=title.id, label="공유인물"))
        await session.commit()
        tv2_id = tv2.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv2_id}/characters")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["label"] == "공유인물"


@pytest.mark.asyncio
async def test_list_characters_404_for_missing_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/characters")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_relationships_for_target_version_includes_character_labels():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        a = Character(title_id=title.id, label="화자")
        b = Character(title_id=title.id, label="상대")
        session.add_all([a, b]); await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id,
                           addressee_character_id=b.id)
        session.add(rel)
        await session.commit()
        tv_id = tv.id
        rel_id = rel.id
        a_id, b_id = a.id, b.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/relationships")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        rel_json = body[0]
        assert rel_json["id"] == rel_id
        assert rel_json["speaker_character_id"] == a_id
        assert rel_json["addressee_character_id"] == b_id
        assert rel_json["speaker_label"] == "화자"
        assert rel_json["addressee_label"] == "상대"
        assert rel_json["confirmed_formality_level"] is None


@pytest.mark.asyncio
async def test_list_relationships_404_for_missing_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/relationships")
        assert r.status_code == 404
