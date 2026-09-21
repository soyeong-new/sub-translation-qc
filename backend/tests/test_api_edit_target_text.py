import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, TargetVersion, Episode, Title, Segment, FindingRow


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_segment(target_text="Hola"):
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안녕하세요", target_text=target_text)
        session.add(seg); await session.commit()
    return seg.id


async def _add_finding(segment_id, status, final_text=""):
    async with async_session() as session:
        segment = await session.get(Segment, segment_id)
        finding = FindingRow(
            target_version_id=segment.target_version_id,
            segment_id=segment_id,
            category="mistranslation",
            description="검수 필요",
            original_text=segment.target_text,
            suggested_text="Hola corregido",
            confidence=0.9,
            status=status,
            final_text=final_text,
        )
        session.add(finding)
        await session.commit()
        return finding.id


@pytest.mark.asyncio
async def test_edit_target_text_updates_segment_without_history():
    seg_id = await _make_segment()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": "Hola, ¿cómo estás?"},
        )
        assert r.status_code == 200
        assert r.json()["target_text"] == "Hola, ¿cómo estás?"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.target_text == "Hola, ¿cómo estás?"


@pytest.mark.asyncio
async def test_edit_target_text_updates_completed_findings_final_text():
    seg_id = await _make_segment()
    finding_id = await _add_finding(seg_id, "approved", "Hola corregido")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": "Hola editado", "reviewer_name": "Soyeong"},
        )

    assert response.status_code == 200
    async with async_session() as session:
        segment = await session.get(Segment, seg_id)
        finding = await session.get(FindingRow, finding_id)
        assert segment.target_text == "Hola editado"
        assert finding.status == "modified"
        assert finding.final_text == "Hola editado"
        assert finding.reviewer_name == "Soyeong"
        assert finding.reviewed_at is not None


@pytest.mark.asyncio
async def test_edit_target_text_rejects_segment_with_pending_finding():
    seg_id = await _make_segment()
    await _add_finding(seg_id, "pending")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": "Hola editado", "reviewer_name": "Soyeong"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_edit_target_text_rejects_line_over_length_limit():
    seg_id = await _make_segment()
    transport = ASGITransport(app=app)
    too_long = "a" * 51
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/edit-target-text",
            json={"target_text": too_long},
        )
        assert r.status_code == 400

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.target_text == "Hola"
