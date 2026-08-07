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
    tv_id, flagged_id = await _make_segment(gender_check_needed=True)
    async with async_session() as session:
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


@pytest.mark.asyncio
async def test_list_flagged_segments_includes_english_pronoun_hint():
    tv_id, seg_id = await _make_segment(
        gender_check_needed=True,
        english_pronoun_hint={"text": "She left.", "he_count": 0, "she_count": 1},
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
    assert r.status_code == 200
    assert r.json()[0]["english_pronoun_hint"] == {
        "text": "She left.", "he_count": 0, "she_count": 1,
    }


@pytest.mark.asyncio
async def test_list_flagged_segments_english_pronoun_hint_defaults_to_none():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
    assert r.status_code == 200
    assert r.json()[0]["english_pronoun_hint"] is None


@pytest.mark.asyncio
async def test_resolve_gender_with_raw_value():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"gender": "female"})
    assert r.status_code == 200
    assert r.json()["resolved_gender_raw"] == "female"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_gender_raw == "female"


@pytest.mark.asyncio
async def test_resolve_formality_with_raw_value():
    tv_id, seg_id = await _make_segment(formality_check_needed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-formality",
                              json={"formality_level": "formal"})
    assert r.status_code == 200
    assert r.json()["resolved_formality_raw"] == "formal"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_formality_raw == "formal"


@pytest.mark.asyncio
async def test_list_flagged_segments_returns_404_for_nonexistent_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/flagged-segments")
    assert r.status_code == 404
