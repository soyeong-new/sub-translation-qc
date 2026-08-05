import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, Segment, Character, Relationship


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _make_segment(**overrides) -> tuple[str, str]:
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                      korean_text="안녕", target_text="hola", **overrides)
        session.add(seg)
        await session.commit()
        return tv.id, seg.id


@pytest.mark.asyncio
async def test_list_flagged_segments_returns_only_flagged_ones():
    tv_id, flagged_id = await _make_segment(
        gender_check_needed=True,
        gender_anchor_candidates=[{"id": "char-1", "label": "민지"}],
    )
    async with async_session() as session:
        title = (await session.execute(
            __import__("sqlalchemy").select(Title)
        )).scalars().first()
        unflagged = Segment(target_version_id=tv_id, index=1, start=2.0, end=3.0,
                            korean_text="뭐해", target_text="que haces")
        session.add(unflagged)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == flagged_id
    assert body[0]["gender_check_needed"] is True
    # 앵커 매칭 후보가 있는 세그먼트는 그대로 내려주고, 없는(None) 세그먼트는
    # 프런트가 다루기 쉽도록 빈 리스트로 정규화한다.
    assert body[0]["gender_anchor_candidates"] == [{"id": "char-1", "label": "민지"}]
    assert body[0]["formality_anchor_candidates"] == []


@pytest.mark.asyncio
async def test_resolve_gender_with_character_id_links_segment():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)
    async with async_session() as session:
        episode_row = (await session.execute(
            __import__("sqlalchemy").select(Episode)
        )).scalars().first()
        char = Character(title_id=episode_row.title_id, label="민지", confirmed_gender="female")
        session.add(char)
        await session.commit()
        char_id = char.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"character_id": char_id})
    assert r.status_code == 200
    assert r.json()["resolved_character_id"] == char_id

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_character_id == char_id
        assert seg.resolved_gender_raw is None


@pytest.mark.asyncio
async def test_resolve_gender_with_raw_value_does_not_link_character():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"gender": "female"})
    assert r.status_code == 200
    assert r.json()["resolved_gender_raw"] == "female"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_gender_raw == "female"
        assert seg.resolved_character_id is None


@pytest.mark.asyncio
async def test_resolve_gender_rejects_both_or_neither_field():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={})
        assert r.status_code == 400
        r = await client.post(f"/segments/{seg_id}/resolve-gender",
                              json={"character_id": "x", "gender": "female"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_resolve_formality_with_relationship_id_links_segment():
    tv_id, seg_id = await _make_segment(formality_check_needed=True)
    async with async_session() as session:
        episode_row = (await session.execute(
            __import__("sqlalchemy").select(Episode)
        )).scalars().first()
        a = Character(title_id=episode_row.title_id, label="민지")
        b = Character(title_id=episode_row.title_id, label="서준")
        session.add_all([a, b])
        await session.flush()
        rel = Relationship(title_id=episode_row.title_id, speaker_character_id=a.id,
                           addressee_character_id=b.id, confirmed_formality_level="informal")
        session.add(rel)
        await session.commit()
        rel_id = rel.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-formality",
                              json={"relationship_id": rel_id})
    assert r.status_code == 200
    assert r.json()["resolved_relationship_id"] == rel_id


@pytest.mark.asyncio
async def test_resolve_formality_with_raw_value():
    tv_id, seg_id = await _make_segment(formality_check_needed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-formality",
                              json={"formality_level": "formal"})
    assert r.status_code == 200
    assert r.json()["resolved_formality_raw"] == "formal"
