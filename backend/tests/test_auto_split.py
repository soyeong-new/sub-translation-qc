import pytest
from sqlalchemy import select
from app.db import engine, async_session
from app.models import Base, FindingRow, Title, Episode, TargetVersion, Segment
from app.repositories import auto_split_overlong_segments

_LONG = "Esto es una frase bastante larga que continúa. Esta es otra frase igualmente larga que sigue aquí."


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _tv(segments):
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        for i, (sid, text, end) in enumerate(segments):
            session.add(Segment(id=sid, target_version_id=tv.id, index=i, start=i * 10.0, end=i * 10.0 + end,
                                korean_text="한국어", target_text=text))
        await session.commit()
        return tv.id


async def _texts():
    async with async_session() as session:
        rows = (await session.execute(select(Segment).order_by(Segment.start))).scalars().all()
    return [r.target_text for r in rows]


@pytest.mark.asyncio
async def test_overlong_segment_is_split_and_short_ones_are_untouched():
    tv = await _tv([("a", "Hola.", 2.0), ("b", _LONG, 6.0)])
    async with async_session() as session:
        assert await auto_split_overlong_segments(session, tv) == 1
        await session.commit()
    texts = await _texts()
    assert len(texts) == 3 and texts[0] == "Hola." and texts[1].startswith("Esto es") and texts[2].startswith("Esta es otra")


@pytest.mark.asyncio
async def test_segment_with_pending_finding_is_left_for_approval_time():
    tv = await _tv([("a", _LONG, 6.0)])
    async with async_session() as session:
        session.add(FindingRow(id="f1", target_version_id=tv, segment_id="a", category="mistranslation",
                               description="근거", original_text="x", suggested_text="y", confidence=0.9))
        await session.commit()
    async with async_session() as session:
        assert await auto_split_overlong_segments(session, tv) == 0
    assert await _texts() == [_LONG]


@pytest.mark.asyncio
async def test_unsplittable_segment_is_left_as_is():
    tv = await _tv([("a", "a" * 100, 6.0), ("b", _LONG, 1.5)])  # 한 단어 / 시간이 2초 미만
    async with async_session() as session:
        assert await auto_split_overlong_segments(session, tv) == 0
    assert await _texts() == ["a" * 100, _LONG]


@pytest.mark.asyncio
async def test_very_long_segment_is_split_again_until_pieces_fit():
    long4 = " ".join(f"Frase número {n} con bastante texto de relleno aquí." for n in range(1, 6))
    tv = await _tv([("a", long4, 20.0)])
    async with async_session() as session:
        assert await auto_split_overlong_segments(session, tv) >= 2
        await session.commit()
    assert all(len(line) <= 42 for t in await _texts() for line in t.split("\n"))
