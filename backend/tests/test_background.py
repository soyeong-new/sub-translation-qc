import asyncio
from datetime import datetime
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, FindingRow, Segment
from app import background

TARGET_SRT = """1
00:00:00,000 --> 00:00:02,000
hola
"""


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_target_version(status="analyzing") -> str:
    async with async_session() as session:
        title = Title(name="T", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status=status)
        session.add(tv)
        await session.commit()
        return tv.id


@pytest.mark.asyncio
async def test_analyze_and_save_stops_at_awaiting_confirmation_without_running_ai_verification(
        tmp_path, monkeypatch):
    """design §AI 검증은 확정된 값을 받고 시작해야 함: 성별/격식 확인이 필요한
    줄이 남아 있으면 analyze_and_save는 거기서 멈추고 S2(Claude/GPT 이중 검증)를
    아예 실행하지 않아야 한다 — 실행해버리면 그 줄은 성별/격식을 추측 없이
    검증한 게 된다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    def _fake_grammar_necessity(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False,
                  "resolved_formality": None, "resolved_gender_from_korean": None,
                  "grammatical_person": None} for p in pairs]

    monkeypatch.setattr("app.core.pipeline.check_grammar_necessity", _fake_grammar_necessity)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video") as mock_delete:
        await background.analyze_and_save(tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "awaiting_confirmation"

        segs = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        assert any(s.gender_check_needed and s.resolved_gender_raw is None for s in segs)

        # S2가 아예 안 돌았어야 한다 — claude/gpt/claude+gpt/안전망이 만드는
        # finding이 하나도 없어야 한다(사전필터 finding은 있을 수 있음).
        findings = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == tv_id)
        )).scalars().all()
        assert all(f.model not in ("claude", "gpt", "claude+gpt", "안전망") for f in findings)

    # 원본 영상은 S1이 끝난 시점에 지운다 — 확인 대기 여부와 무관하다.
    mock_delete.assert_called_once_with("/x.mp4")


@pytest.mark.asyncio
async def test_analyze_and_save_sets_status_review_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video") as mock_delete:
        await background.analyze_and_save(tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "review"
        assert tv.error_message is None
    # C2 회귀: 원본 삭제는 결과가 실제로 커밋된 뒤에만 일어나야 한다.
    mock_delete.assert_called_once_with("/x.mp4")


@pytest.mark.asyncio
async def test_analyze_and_save_does_not_delete_original_video_when_pipeline_fails(
        tmp_path, monkeypatch):
    """C2 회귀: run_pipeline_phase1이 실패하면(예: 타임아웃, 예외) 원본 영상이 지워지면
    안 된다 — 지워버리면 프록시 경로도 저장되지 않았는데 원본까지 없어서
    /run-analysis 재시도가 영영 실패하게 된다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    with patch("app.background.run_pipeline_phase1", side_effect=RuntimeError("STT 실패")), \
         patch("app.background.delete_original_video") as mock_delete:
        await background.analyze_and_save(tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_and_save_keeps_review_status_when_delete_original_video_raises(
        tmp_path, monkeypatch):
    """회귀 테스트: 분석 결과가 이미 커밋된 뒤 원본 삭제(delete_original_video)가
    실패해도(예: 권한 문제로 unlink가 FileNotFoundError가 아닌 다른 OSError를
    던지는 경우), 이미 성공한 분석 결과를 "실패"로 덮어써서는 안 된다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video",
               side_effect=PermissionError("접근 거부")):
        await background.analyze_and_save(tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "review"
        assert tv.error_message is None


@pytest.mark.asyncio
async def test_analyze_and_save_persists_when_gpt_reintroduces_ellipsis_on_same_segment(
        tmp_path, monkeypatch):
    """회귀 테스트(critical): GPT 2차가 문장을 늘리며 이미 온점 자동보정을 거친
    세그먼트에 새 온점(4개 이상)을 만들면, 최초 체크와 GPT 이후 최종 재체크가
    같은 segment_id에 대해 "ellipsis" FormatViolation을 하나씩 만든다.
    repositories.py가 이 (segment_id, rule) 조합을 구분하지 못했을 때는
    두 번째 저장에서 findings_pkey UNIQUE 제약을 위반해 save_pipeline_result가
    IntegrityError를 던졌고, analyze_and_save의 except Exception이 그걸 잡아
    전체 target_version을 failed로 처리했다 — STT + Claude/GPT 두 LLM 패스
    비용이 이미 다 든 뒤에 결과 전체를 날리는 버그였다. run_pipeline의 in-memory
    반환값만 보는 test_pipeline.py의 assertion은 이 버그를 잡지 못한다 —
    실제로 save_pipeline_result를 거쳐야 재현된다. 이 테스트는 background.
    analyze_and_save를 실제로 실행해 DB까지 거친 뒤 status가 "review"로
    끝나는지 (즉, IntegrityError 없이 두 finding이 모두 저장됐는지) 확인한다."""
    from app.providers.mock import MockProvider

    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBAD_TRANSLATION aquí....\n", encoding="utf-8")

    async def _introduces_ellipsis(self, pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "espera......", "description": "GPT가 늘어뜨림"}]

    # get_provider()가 매번 새 MockProvider 인스턴스를 만들므로, 인스턴스가
    # 아니라 클래스에 패치해야 analyze_and_save 내부에서 실제로 쓰이는 provider
    # 에도 적용된다. Claude/GPT 둘 다 같은 문구를 내야 MockProvider의 기본
    # 동등성 판정(text_a == text_b)이 true가 되어 진짜 합의로 확정되고
    # 실제로 적용된다.
    monkeypatch.setattr(MockProvider, "correct_primary", _introduces_ellipsis)
    monkeypatch.setattr(MockProvider, "verify_and_refine", _introduces_ellipsis)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video", return_value=None):
        await background.analyze_and_save(tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "review"
        assert tv.error_message is None

        # 회귀(important, 후속 리뷰): 두 finding의 original_text가 파이프라인
        # 최종 상태 하나로 뭉개지지 않고 각 체크포인트 고유의 "고치기 전" 텍스트를
        # 유지해야 한다. 최초 체크포인트는 Claude/GPT 이전 원문("BAD_TRANSLATION
        # aquí...."가 온점 자동보정된 "BAD_TRANSLATION aquí..."), 두 번째(S4
        # 최종 재체크)는 GPT가 늘어뜨린 뒤("espera......") 값이어야 하며 서로
        # 달라야 한다. 최종 pair 텍스트("espera...") 하나로 재구성됐다면 이
        # 검증이 실패한다.
        rows = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == tv_id,
                                      FindingRow.category == "formatting")
        )).scalars().all()
        assert len(rows) == 2
        original_texts = {r.original_text for r in rows}
        assert original_texts == {"BAD_TRANSLATION aquí....", "espera......"}


@pytest.mark.asyncio
async def test_analyze_and_save_sets_status_failed_on_exception(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    with patch("app.background.run_pipeline_phase1", side_effect=RuntimeError("STT 실패")):
        await background.analyze_and_save(tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message == "STT 실패"


@pytest.mark.asyncio
async def test_analyze_and_save_sets_status_failed_on_timeout(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr(background, "ANALYSIS_TIMEOUT_SECONDS", 0.01)
    tv_id = await _make_target_version()

    async def _slow_pipeline(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {}

    with patch("app.background.run_pipeline_phase1", side_effect=_slow_pipeline):
        await background.analyze_and_save(tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message == "분석 시간 초과 (1시간)"


@pytest.mark.asyncio
async def test_analyze_and_save_does_not_raise_when_target_version_missing(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    await background.analyze_and_save("does-not-exist", "/nonexistent.srt")


@pytest.mark.asyncio
async def test_analyze_and_save_persists_stt_cache_on_first_success(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video"):
        await background.analyze_and_save(tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        assert episode.stt_cache == {
            "segments": [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}],
            "granularity": "word",
        }
        assert episode.video_proxy_path == "/fake/proxy.mp4"


@pytest.mark.asyncio
async def test_analyze_and_save_reuses_stt_cache_and_skips_transcribe(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        episode.stt_cache = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "캐시된 단어"}],
            "granularity": "word",
        }
        episode.video_proxy_path = "/fake/cached_proxy.mp4"
        await session.commit()

    with patch("app.core.pipeline.extract_audio") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy") as mock_proxy, \
         patch("app.background.delete_original_video"):
        await background.analyze_and_save(tv_id, str(srt_path))

    mock_extract.assert_not_called()
    mock_proxy.assert_not_called()
    async with async_session() as session:
        segs = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        assert any(s.korean_text == "캐시된 단어" for s in segs)


@pytest.mark.asyncio
async def test_analyze_and_save_ignores_stale_stt_cache_missing_granularity_tag(tmp_path, monkeypatch):
    """캐시 형식이 바뀌면(문장→단어 단위) 옛 형식으로 저장된 캐시를 그대로
    믿으면 안 된다 — granularity 태그가 없는(옛) 캐시는 무시하고 다시
    STT를 돌려야 하고, 그 결과로 캐시도 새 형식으로 갱신돼야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        episode.stt_cache = {"segments": [{"start": 0.0, "end": 1.0, "text": "옛 형식 캐시"}]}
        episode.video_proxy_path = "/fake/cached_proxy.mp4"
        await session.commit()

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4") as mock_proxy, \
         patch("app.background.delete_original_video"):
        await background.analyze_and_save(tv_id, str(srt_path))

    mock_extract.assert_called_once()
    mock_proxy.assert_called_once()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        assert episode.stt_cache["granularity"] == "word"
        segs = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        assert not any(s.korean_text == "옛 형식 캐시" for s in segs)


@pytest.mark.asyncio
async def test_analyze_and_save_uses_episode_korean_srt_path_and_skips_transcribe(
        tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕\n", encoding="utf-8",
    )

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        episode.korean_srt_path = str(ko_srt_path)
        await session.commit()

    with patch("app.core.pipeline.extract_audio") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video"):
        await background.analyze_and_save(tv_id, str(srt_path))

    mock_extract.assert_not_called()
    async with async_session() as session:
        segs = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        assert any(s.korean_text == "안녕" for s in segs)


@pytest.mark.asyncio
async def test_analyze_and_save_persists_detected_video_offset(tmp_path, monkeypatch):
    """회귀: 영상을 잘라 올려 STT-SRT 사이 상수 오프셋이 감지되면, 그 값이
    TargetVersion.video_offset_seconds에 저장돼야 한다 — 프론트가 영상
    미리보기 seek 시 이 값으로 SRT 시계를 영상 파일 시계로 변환한다."""
    from app.core.ingest import build_srt

    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    entries = [
        {"start": start, "end": start + 5.0, "text": f"Linea {i}"}
        for i, start in enumerate([0.0, 13.0, 41.0, 68.0, 100.0, 155.0])
    ]
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(build_srt(entries), encoding="utf-8")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        episode = await session.get(Episode, tv.episode_id)
        episode.stt_cache = {
            "segments": [
                {"start": e["start"] - 55.0 + 1.0, "end": e["start"] - 55.0 + 1.5, "text": f"단어{i}"}
                for i, e in enumerate(entries)
            ],
            "granularity": "word",
        }
        episode.video_proxy_path = "/fake/cached_proxy.mp4"
        await session.commit()

    with patch("app.core.pipeline.extract_audio") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy") as mock_proxy, \
         patch("app.background.delete_original_video"):
        await background.analyze_and_save(tv_id, str(srt_path))

    mock_extract.assert_not_called()
    mock_proxy.assert_not_called()
    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.video_offset_seconds == pytest.approx(55.0, abs=1.5)


