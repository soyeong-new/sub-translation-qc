import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from sqlalchemy import select
from app.models import (
    Base, Title, Episode, TargetVersion, Segment, FindingRow, ExportRow,
    GlossaryEntry, GlossarySpelling,
)


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
                       category="mistranslation", description="근거",
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
        assert body["name_variant_groups"] == []


@pytest.mark.asyncio
async def test_export_returns_name_variant_groups():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="fr", variant="FR")
        session.add(tv); await session.flush()
        for i, text in enumerate(["Où est Jan Di ?", "Jan-Di, viens.", "Ça va, Jan-Di ?"]):
            session.add(Segment(target_version_id=tv.id, index=i, start=float(i), end=i + 1.0,
                                korean_text="잔디야", target_text=text))
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
    [group] = body["name_variant_groups"]
    assert group["recommended"] == "Jan-Di"
    assert [v["surface"] for v in group["variants"]] == ["Jan-Di", "Jan Di"]
    assert sum(1 for l in group["lines"] if "Jan Di" in l["surfaces"]) == 1


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
    long_text = "x" * 60  # exceeds format_rules.MAX_LINE_CHARS (42), no findings involved
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        # duration을 넉넉히 줘서(20초) 읽기 속도 위반은 안 걸리고 줄 길이
        # 위반만 걸리게 한다 — 이 테스트는 line_length 단독 감지를 검증한다.
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=20.0,
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
async def test_export_flags_glossary_mismatch_for_finding_less_segment(monkeypatch):
    """회귀(사용자 보고) 안전망: finding 자체가 없어(검수 시점 훅이 닿지
    않은) 등록된 고유명사 표준 표기가 안 지켜진 세그먼트도 export
    시점에 참고용 경고로 걸려야 한다(non-blocking). MockProvider의
    2차 판정은 korean_text에 "대명사"가 없으면 위반으로 취급한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        entry = GlossaryEntry(title_id=title.id, korean_term="강오크",
                               category="person", aliases=[])
        session.add(entry); await session.flush()
        session.add(GlossarySpelling(entry_id=entry.id, language="es", variant="LATAM",
                                      canonical="Kang-ok"))
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=5.0,
                      korean_text="야, 강오크.", target_text="Oye, Gang-ok.")
        session.add(seg); await session.flush()
        await session.commit()
        tv_id, seg_id = tv.id, seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200  # non-blocking
        body = r.json()
        glossary_warnings = [w for w in body["format_warnings"] if w["rule"] == "glossary_mismatch"]
        assert len(glossary_warnings) == 1
        assert glossary_warnings[0]["segment_id"] == seg_id


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
async def test_export_check_does_not_record_an_export_row():
    """검사(GET /export)만으로는 exports 테이블에 감사 기록이 남으면 안 된다
    — LLM까지 도는 검사 호출마다 기록되면 실제로 내보낸 횟수를 알 수 없다.
    감사 기록은 /export/confirm(실제 다운로드 시점)에서만 남는다."""
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200

    async with async_session() as session:
        rows = list((await session.execute(
            select(ExportRow).where(ExportRow.target_version_id == tv_id)
        )).scalars().all())
    assert rows == []


@pytest.mark.asyncio
async def test_export_confirm_records_an_export_row():
    """실제로 다운로드했을 때(export/confirm)만 exports 테이블에 감사 기록이
    남아야 한다. 저장된 통계는 검사 응답의 stats와 정확히 일치해야 한다."""
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
                       category="mistranslation", description="근거",
                       original_text="texto malo", suggested_text="texto bueno",
                       confidence=0.9, status="approved", final_text="texto bueno"),
            FindingRow(id="fb", target_version_id=tv.id, segment_id=seg.id,
                       category="mistranslation", description="근거",
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
        r = await client.post(f"/target-versions/{tv_id}/export/confirm")
        assert r.status_code == 200

    async with async_session() as session:
        rows = list((await session.execute(
            select(ExportRow).where(ExportRow.target_version_id == tv_id)
        )).scalars().all())

    assert len(rows) == 1
    assert rows[0].finding_count == stats["finding_count"] == 2
    assert rows[0].reflection_rate == stats["reflection_rate"] == 0.5
    assert rows[0].exported_at is not None


@pytest.mark.asyncio
async def test_export_returns_filename_with_title_and_language_when_no_episode():
    async with async_session() as session:
        title = Title(name="보통의 연애", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        assert r.json()["filename"] == "보통의 연애_es_LATAM.srt"


@pytest.mark.asyncio
async def test_export_returns_filename_with_episode_when_present():
    async with async_session() as session:
        title = Title(name="보통의 연애", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, episode_no=3, video_path="/x.mp4")
        session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/export")
        assert r.status_code == 200
        assert r.json()["filename"] == "보통의 연애_3화_es_LATAM.srt"


@pytest.mark.asyncio
async def test_export_returns_404_for_unknown_target_version():
    """존재하지 않는 target_version_id로 export하면 404여야 한다."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/export")

    assert r.status_code == 404
    assert r.json()["detail"] == "target version not found"


@pytest.mark.asyncio
async def test_export_confirm_returns_404_for_unknown_target_version():
    """존재하지 않는 target_version_id로 confirm하면 404여야 한다. ExportRow에
    target_versions를 참조하는 FK가 있어, 가드가 없으면 감사 행을 넣는 순간
    IntegrityError가 그대로 터져 500이 된다."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/target-versions/does-not-exist/export/confirm")

    assert r.status_code == 404
    assert r.json()["detail"] == "target version not found"

    # 실패한 confirm은 감사 행을 남기지 않는다.
    async with async_session() as session:
        rows = list((await session.execute(select(ExportRow))).scalars().all())
    assert rows == []


@pytest.mark.asyncio
async def test_export_keeps_video_proxy_file(tmp_path):
    """회귀(사용자 재현): export는 더 이상 영상 프록시를 지우지 않는다 —
    export 후에도 계속 검토/재수정하는 흐름이 흔한데, 한 번 내보냈다고
    미리보기가 영구히 사라지면 안 된다(원본 영상은 이미 삭제된 뒤라
    복구 불가능하다). 삭제는 title을 실제로 지울 때만 한다."""
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

    assert proxy_path.exists()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.video_proxy_path == str(proxy_path)


@pytest.mark.asyncio
async def test_export_warns_about_unconfirmed_amount_until_spelling_is_filled(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        entry = GlossaryEntry(title_id=title.id, korean_term="100억", category="amount", aliases=[])
        session.add(entry); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=5.0,
                      korean_text="대신 100억", target_text="A cambio de dinero")
        session.add(seg); await session.flush()
        await session.commit()
        tv_id, seg_id, entry_id = tv.id, seg.id, entry.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        warned = [w for w in body["format_warnings"]
                  if w["rule"] == "glossary_mismatch" and w["segment_id"] == seg_id]
        assert len(warned) == 1 and "100억" in warned[0]["detail"]

        await client.patch(f"/glossary/{entry_id}",
                           json={"spellings": {"es_LATAM": "10 mil millones de wones"}})
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert not any("아직 비어 있음" in w["detail"] for w in body["format_warnings"])


async def _make_glossary_warning_version():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="fr", variant="FR")
        session.add(tv); await session.flush()
        session.add(Segment(id="p1", target_version_id=tv.id, index=0, start=0.0, end=2.0,
                            korean_text="야, 강오크.", target_text="Oye, Gang-ok."))
        entry = GlossaryEntry(title_id=title.id, korean_term="강오크", category="person", aliases=[])
        session.add(entry); await session.flush()
        session.add(GlossarySpelling(entry_id=entry.id, language="fr", variant="FR", canonical="Kang-ok"))
        await session.commit()
        return tv.id


def _glossary_warnings(body):
    return [w for w in body["format_warnings"] if w["rule"] == "glossary_mismatch"]


@pytest.mark.asyncio
async def test_glossary_fix_apply_and_undo_flow():
    tv_id = await _make_glossary_warning_version()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        warning = _glossary_warnings(body)[0]
        assert (warning["korean_term"], warning["canonical"]) == ("강오크", "Kang-ok")
        assert "Gang-ok" in body["srt"]

        # 적용 → 최종 SRT가 등록 표기가 되고 경고가 사라진다
        r = await client.post(f"/target-versions/{tv_id}/glossary-fixes", json={"items": [
            {"segment_id": "p1", "replacements": [{"from_text": "Gang-ok", "to_text": "Kang-ok"}]}]})
        assert r.json() == {"applied": 1, "skipped": []}
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert "Kang-ok" in body["srt"] and "Gang-ok" not in body["srt"]
        assert _glossary_warnings(body) == []
        assert body["glossary_fixes"] == [{"segment_id": "p1", "before_text": "Oye, Gang-ok.",
                                           "after_text": "Oye, Kang-ok."}]
        # 바꿀 표기가 이미 없으면 건너뛴다
        r = await client.post(f"/target-versions/{tv_id}/glossary-fixes", json={"items": [
            {"segment_id": "p1", "replacements": [{"from_text": "Gang-ok", "to_text": "Kang-ok"}]}]})
        assert r.json()["skipped"] == ["p1"]

        # 가벼운 조회(내보내기 검사 없이)로도 적용 내역이 보인다 — 화면의 전체 자막이 바로 쓴다
        r = await client.get(f"/target-versions/{tv_id}/applied-fixes")
        assert r.json() == [{"segment_id": "p1", "kind": "applied", "before_text": "Oye, Gang-ok.",
                             "after_text": "Oye, Kang-ok."}]

        # 되돌리기 → 원래 문구와 경고가 돌아온다
        await client.post(f"/target-versions/{tv_id}/glossary-fixes/undo", json={"segment_id": "p1"})
        assert (await client.get(f"/target-versions/{tv_id}/applied-fixes")).json() == []
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert "Gang-ok" in body["srt"] and body["glossary_fixes"] == []
        assert len(_glossary_warnings(body)) == 1


async def _make_repeated_version():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="fr", variant="FR")
        session.add(tv); await session.flush()
        session.add(Segment(id="r1", target_version_id=tv.id, index=0, start=10.0, end=12.0,
                            korean_text="아빠는 팔 안 아파", target_text="Papa n'a pas mal au bras"))
        session.add(Segment(id="r2", target_version_id=tv.id, index=1, start=50.0, end=52.0,
                            korean_text="아빠는 팔 안 아파!", target_text="Papa ne ressent rien"))
        await session.commit()
        return tv.id


@pytest.mark.asyncio
async def test_repeated_fix_apply_and_undo_flow():
    tv_id = await _make_repeated_version()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert [m["segment_id"] for m in body["repeated_groups"][0]["members"]] == ["r1", "r2"]

        # r2를 r1의 번역으로 맞춘다 → 묶음이 사라지고 최종 SRT에 반영된다
        r = await client.post(f"/target-versions/{tv_id}/repeated-fixes", json={"items": [
            {"segment_id": "r2", "new_text": "Papa n'a pas mal au bras"}]})
        assert r.json() == {"applied": 1, "skipped": []}
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert body["repeated_groups"] == []
        assert "Papa ne ressent rien" not in body["srt"]
        assert body["repeated_fixes"] == [{"segment_id": "r2", "before_text": "Papa ne ressent rien",
                                           "after_text": "Papa n'a pas mal au bras"}]
        assert body["glossary_fixes"] == []  # 용어집 수정 목록과 섞이지 않는다

        # 이미 같은 문구면 건너뛴다
        r = await client.post(f"/target-versions/{tv_id}/repeated-fixes", json={"items": [
            {"segment_id": "r2", "new_text": "Papa n'a pas mal au bras"}]})
        assert r.json()["skipped"] == ["r2"]

        await client.post(f"/target-versions/{tv_id}/repeated-fixes/undo", json={"segment_id": "r2"})
        body = (await client.get(f"/target-versions/{tv_id}/export")).json()
        assert "Papa ne ressent rien" in body["srt"] and len(body["repeated_groups"]) == 1


@pytest.mark.asyncio
async def test_reassemble_returns_same_payload_as_export():
    tv_id = await _make_repeated_version()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        exported = (await client.get(f"/target-versions/{tv_id}/export")).json()
        r = await client.get(f"/target-versions/{tv_id}/export/reassemble")
    assert r.status_code == 200 and r.json() == exported
