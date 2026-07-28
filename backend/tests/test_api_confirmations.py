import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Character, Relationship


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_confirm_gender_persists_and_is_reused_across_episodes():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        char = Character(title_id=title.id, label="인물1"); session.add(char)
        await session.commit()
        char_id = char.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/characters/{char_id}/confirm-gender",
                               json={"gender": "female"})
        assert r.status_code == 200
        assert r.json()["confirmed_gender"] == "female"


@pytest.mark.asyncio
async def test_confirm_formality_stores_language_neutral_value():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        a = Character(title_id=title.id, label="A"); session.add(a)
        b = Character(title_id=title.id, label="B"); session.add(b)
        await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id,
                           addressee_character_id=b.id)
        session.add(rel)
        await session.commit()
        rel_id = rel.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/relationships/{rel_id}/confirm-formality",
                               json={"formality_level": "informal"})
        assert r.status_code == 200
        assert r.json()["confirmed_formality_level"] == "informal"
