import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from sqlalchemy import select
from app.models import Base, Title, Episode, TargetVersion, Segment, FindingRow, ExportRow


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


@pytest.mark.asyncio
async def test_export_skips_empty_segments_and_orders_by_start_time():
    """정렬 실패로 target_text가 빈 세그먼트는 빈 큐로 나가면 안 되고, 세그먼트는
    저장 순서(index)가 아니라 타임코드(start) 순으로 나가야 한다."""
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        # index 순서와 start 순서를 일부러 어긋나게 저장한다 (align()이 짝 없는
        # 대상언어 세그먼트를 목록 뒤에 붙이는 상황 재현).
        session.add_all([
            Segment(target_version_id=tv.id, index=0, start=10.0, end=12.0,
                    korean_text="한국어", target_text="tercero"),
            Segment(target_version_id=tv.id, index=1, start=5.0, end=7.0,
                    korean_text="한국어", target_text=""),  # 한국어 전용 고아 세그먼트
            Segment(target_version_id=tv.id, index=2, start=0.0, end=2.0,
                    korean_text="", target_text="primero"),
        ])
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        srt = r.json()["srt"]

    assert srt.count("-->") == 2  # 빈 세그먼트는 큐로 나가지 않는다
    assert "00:00:05,000 --> 00:00:07,000" not in srt
    assert srt.index("primero") < srt.index("tercero")  # start 순서


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


@pytest.mark.asyncio
async def test_export_records_an_export_row():
    """export 이력은 exports 테이블에 남아야 한다 (감사 기록). 저장된 통계는
    응답의 stats와 정확히 일치해야 한다."""
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="", target_text="texto malo")
        session.add(seg); await session.flush()
        session.add_all([
            FindingRow(id="fa", target_version_id=tv.id, segment_id=seg.id,
                       category="translation", description="근거",
                       original_text="texto malo", suggested_text="texto bueno",
                       confidence=0.9, status="approved", final_text="texto bueno"),
            FindingRow(id="fb", target_version_id=tv.id, segment_id=seg.id,
                       category="translation", description="근거",
                       original_text="texto malo", suggested_text="otro",
                       confidence=0.5, status="pending"),
        ])
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        stats = r.json()["stats"]

    async with async_session() as session:
        rows = list((await session.execute(
            select(ExportRow).where(ExportRow.target_version_id == tv_id)
        )).scalars().all())

    assert len(rows) == 1
    assert rows[0].finding_count == stats["finding_count"] == 2
    assert rows[0].reflection_rate == stats["reflection_rate"] == 0.5
    assert rows[0].exported_at is not None


@pytest.mark.asyncio
async def test_export_returns_404_for_unknown_target_version():
    """존재하지 않는 target_version_id로 export하면 404여야 한다. ExportRow에
    target_versions를 참조하는 FK가 있어, 가드가 없으면 감사 행을 넣는 순간
    IntegrityError가 그대로 터져 500이 된다."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/export")

    assert r.status_code == 404
    assert r.json()["detail"] == "target version not found"

    # 실패한 export는 감사 행을 남기지 않는다.
    async with async_session() as session:
        rows = list((await session.execute(select(ExportRow))).scalars().all())
    assert rows == []


@pytest.mark.asyncio
async def test_export_deletes_video_proxy_file(tmp_path):
    proxy_path = tmp_path / "proxy.mp4"
    proxy_path.write_bytes(b"fake video bytes")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           video_proxy_path=str(proxy_path))
        session.add(tv); await session.flush()
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200

    assert not proxy_path.exists()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.video_proxy_path is None
