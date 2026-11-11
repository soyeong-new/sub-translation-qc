import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import Base, FindingRow, Title, Episode, TargetVersion, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _segment(text, end=6.0, with_approved_finding=False):
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
        session.add(Segment(id="s1", target_version_id=tv.id, index=0, start=0.0, end=end,
                            korean_text="한국어", target_text=text))
        await session.flush()
        if with_approved_finding:
            session.add(FindingRow(id="f1", target_version_id=tv.id, segment_id="s1",
                                   category="mistranslation", description="근거", original_text="a",
                                   suggested_text="b", confidence=0.9, status="approved", final_text=text))
        await session.commit()


async def _split(text=None, segment_id="s1"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return (await client.post(f"/segments/{segment_id}/split", json={"target_text": text or ""})).json()


@pytest.mark.asyncio
async def test_split_divides_text_instead_of_copying_it_and_splits_time_by_characters():
    await _segment("- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora.", end=5.0)
    body = await _split()
    assert body["new"]["target_text"].startswith("No, gracias")
    async with async_session() as session:
        seg = await session.get(Segment, "s1")
    assert seg.target_text == "Sí, ya lo sé."
    assert seg.end == body["new"]["start"] and body["new"]["end"] == 5.0 and 0 < seg.end < 5.0


@pytest.mark.asyncio
async def test_split_keeps_old_behavior_when_text_cannot_be_split():
    await _segment("Hola. ¿Cómo estás?", end=1.5)
    body = await _split()
    assert body["new"]["target_text"] == "Hola. ¿Cómo estás?"
    assert body["original"]["end"] == 0.75


@pytest.mark.asyncio
async def test_split_updates_approved_finding_final_text_to_first_piece():
    await _segment("- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora.",
                   end=5.0, with_approved_finding=True)
    await _split()
    async with async_session() as session:
        finding = (await session.execute(select(FindingRow))).scalar_one()
    assert finding.final_text == "Sí, ya lo sé." and finding.status == "modified"


async def _undo(segment_id):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/segments/{segment_id}/undo-split")


@pytest.mark.asyncio
async def test_undo_split_restores_original_text_end_and_finding():
    text = "- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora."
    await _segment(text, end=5.0, with_approved_finding=True)
    body = await _split()
    r = await _undo(body["new"]["id"])
    assert r.status_code == 200
    async with async_session() as session:
        segs = (await session.execute(select(Segment))).scalars().all()
        finding = (await session.execute(select(FindingRow))).scalar_one()
    assert len(segs) == 1 and segs[0].target_text == text and segs[0].end == 5.0
    assert finding.status == "approved" and finding.final_text == text


@pytest.mark.asyncio
async def test_undo_split_also_restores_a_copy_style_split():
    await _segment("Hola. ¿Cómo estás?", end=1.5)
    body = await _split()
    assert (await _undo(body["new"]["id"])).status_code == 200
    async with async_session() as session:
        seg = await session.get(Segment, "s1")
    assert seg.end == 1.5


@pytest.mark.asyncio
async def test_undo_split_refuses_a_segment_that_was_not_made_by_split():
    await _segment("Hola.")
    assert (await _undo("s1")).status_code == 409


@pytest.mark.asyncio
async def test_undo_split_refuses_when_the_new_cue_has_a_finding():
    await _segment("- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora.", end=5.0)
    body = await _split()
    async with async_session() as session:
        seg = await session.get(Segment, body["new"]["id"])
        session.add(FindingRow(id="f9", target_version_id=seg.target_version_id, segment_id=seg.id,
                               category="mistranslation", description="근거", original_text="a",
                               suggested_text="b", confidence=0.9))
        await session.commit()
    assert (await _undo(body["new"]["id"])).status_code == 409


@pytest.mark.asyncio
async def test_undo_split_refuses_when_the_new_cue_was_split_again():
    await _segment("Uno dos tres cuatro. Cinco seis siete ocho. Nueve diez once doce. Trece catorce quince.", end=12.0)
    first = await _split()
    await _split(segment_id=first["new"]["id"])
    assert (await _undo(first["new"]["id"])).status_code == 409


@pytest.mark.asyncio
async def test_list_segments_lets_both_pieces_of_a_split_undo_it():
    await _segment("- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora.", end=5.0)
    body = await _split()
    new_id = body["new"]["id"]
    async with async_session() as session:
        tv_id = (await session.get(Segment, "s1")).target_version_id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rows = (await client.get(f"/target-versions/{tv_id}/segments")).json()
    assert {r["id"]: r["undo_split_id"] for r in rows} == {"s1": new_id, new_id: new_id}
    assert all(r["can_undo_split"] for r in rows)
