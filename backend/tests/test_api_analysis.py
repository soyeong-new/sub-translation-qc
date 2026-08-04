import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Base, Title, Episode, TargetVersion

TARGET_SRT = """1
00:00:00,000 --> 00:00:02,000
BAD_TRANSLATION aquí
"""


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_run_analysis_then_list_findings(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="pending"); session.add(tv)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/target-versions/{tv_id}/run-analysis",
                json={"target_srt_path": str(srt_path)},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "analyzing"

            # background task들이 완료될 때까지 대기한다.
            # 패치가 활성 상태인 동안 waiting하므로, background task가
            # extract_audio를 호출할 때 여전히 mock이 활성이다.
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}/findings")
            assert r.status_code == 200
            findings = r.json()
            assert any(f["category"] == "translation" for f in findings)


@pytest.mark.asyncio
async def test_run_analysis_returns_404_when_episode_missing(tmp_path, monkeypatch):
    """episode 조회 결과를 None 체크 없이 쓰면 episode.video_path 접근에서
    AttributeError(500)가 났다. 깨진 불변식이라도 다른 누락 리소스와 동일하게
    404로 나가야 한다.

    target_versions.episode_id에는 FK가 걸려 있어 DB 상태만으로는 이 상황을
    만들 수 없으므로, Episode 조회만 None을 돌려주도록 가로채 방어 로직을
    직접 겨냥한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="pending"); session.add(tv)
        await session.commit()
        tv_id = tv.id

    real_get = AsyncSession.get

    async def get_with_missing_episode(self, entity, ident, *args, **kwargs):
        if entity is Episode:
            return None
        return await real_get(self, entity, ident, *args, **kwargs)

    transport = ASGITransport(app=app)
    with patch.object(AsyncSession, "get", get_with_missing_episode):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/target-versions/{tv_id}/run-analysis",
                                  json={"target_srt_path": str(srt_path)})
    assert r.status_code == 404
    assert r.json()["detail"] == "episode not found"


@pytest.mark.asyncio
async def test_run_analysis_can_be_retried_without_integrity_error(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="pending"); session.add(tv)
        await session.commit()
        tv_id = tv.id

    # GET /target-versions/{id}가 video_proxy_url을 MEDIA_ROOT/video_proxy
    # 기준 상대경로로 계산하므로("/fake/proxy.mp4" 같은 경로를 주면
    # Path.relative_to()가 ValueError를 던진다 — 이 테스트가 검증하려는
    # PK 충돌 버그와는 무관한 별개의 기존 동작), 실제 generate_video_proxy가
    # 만드는 것과 같은 형태로 MEDIA_ROOT/video_proxy 하위 경로를 mock한다.
    from app.core.uploads import MEDIA_ROOT
    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy.mp4")

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value=fake_proxy_path), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(2):
                r = await client.post(
                    f"/target-versions/{tv_id}/run-analysis",
                    json={"target_srt_path": str(srt_path)},
                )
                assert r.status_code == 200
                if app.state.background_tasks:
                    await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.status_code == 200
            assert r.json()["status"] == "review"


@pytest.mark.asyncio
async def test_get_target_version_exposes_pipeline_warnings(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="pending"); session.add(tv)
        await session.commit()
        tv_id = tv.id

    from app.providers.mock import MockProvider

    async def _analyze_characters_raises(*args, **kwargs):
        raise RuntimeError("인물 식별 API 오류")

    monkeypatch.setattr(MockProvider, "analyze_characters", _analyze_characters_raises)

    # GET /target-versions/{id}가 video_proxy_url을 MEDIA_ROOT/video_proxy
    # 기준 상대경로로 계산하므로("/fake/proxy.mp4" 같은 경로를 주면
    # Path.relative_to()가 ValueError를 던진다 — 이 테스트가 검증하려는
    # warnings 노출과는 무관한 별개의 기존 동작), 실제 generate_video_proxy가
    # 만드는 것과 같은 형태로 MEDIA_ROOT/video_proxy 하위 경로를 mock한다.
    from app.core.uploads import MEDIA_ROOT
    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy_warnings.mp4")

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value=fake_proxy_path), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/target-versions/{tv_id}/run-analysis",
                              json={"target_srt_path": str(srt_path)})
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
    assert r.status_code == 200
    assert r.json()["warnings"] == [{"stage": "인물 식별", "message": "인물 식별 API 오류"}]
