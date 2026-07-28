import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, Segment, FindingRow


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_export_returns_srt_and_stats():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="", target_text="texto malo")
        session.add(seg); await session.flush()
        f = FindingRow(id="f1", target_version_id=tv.id, segment_id=seg.id,
                       category="translation", description="근거",
                       original_text="texto malo", suggested_text="texto bueno",
                       confidence=0.9, status="approved", final_text="texto bueno")
        session.add(f)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        body = r.json()
        assert "texto bueno" in body["srt"]
        assert body["stats"]["finding_count"] == 1
        assert body["stats"]["reflection_rate"] == 1.0


# --- Scope addition: safety-net check (design §5-1 point 3) must be wired
# into the export endpoint. The brief's own export.py defines
# safety_net_check() explicitly as "export 직전 안전망 (지점 3)" but the
# brief's literal endpoint code never called it. These tests prove
# format_warnings is populated (non-blocking) from the SAME final text that
# ends up in the exported SRT, and empty when there's nothing to flag.

@pytest.mark.asyncio
async def test_export_format_warnings_flags_line_length_violation_with_no_findings():
    long_text = "x" * 60  # exceeds format_rules.MAX_LINE_CHARS (50), no findings involved
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="", target_text=long_text)
        session.add(seg); await session.flush()
        await session.commit()
        tv_id = tv.id
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200  # non-blocking: still 200 despite the violation
        body = r.json()
        assert body["stats"]["finding_count"] == 0
        assert len(body["format_warnings"]) == 1
        assert body["format_warnings"][0]["segment_id"] == seg_id
        assert body["format_warnings"][0]["rule"] == "line_length"


@pytest.mark.asyncio
async def test_export_format_warnings_empty_when_clean():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="", target_text="texto corto")
        session.add(seg); await session.flush()
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        body = r.json()
        assert body["format_warnings"] == []


@pytest.mark.asyncio
async def test_export_format_warnings_checks_final_text_not_original():
    long_text = "y" * 60  # original violates line length
    short_fixed = "texto corregido"  # final_text (post-approval) does not
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="", target_text=long_text)
        session.add(seg); await session.flush()
        f = FindingRow(id="f2", target_version_id=tv.id, segment_id=seg.id,
                       category="formatting", description="근거",
                       original_text=long_text, suggested_text=short_fixed,
                       confidence=0.9, status="approved", final_text=short_fixed)
        session.add(f)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        body = r.json()
        assert short_fixed in body["srt"]
        assert body["format_warnings"] == []
