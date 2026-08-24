import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Base, Title, Episode, TargetVersion, Segment
from app.repositories import get_character_gender_facts

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
            assert any(f["category"] == "mistranslation" for f in findings)


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
async def test_rerun_reuses_stored_srt_path_without_new_upload(tmp_path, monkeypatch):
    """"새로고침" 버튼 — run-analysis 때 저장해둔 target_srt_path를 그대로
    써서 다시 도는지 확인한다. rerun 요청은 body가 없다(파일 재업로드 없음)."""
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

    from app.core.uploads import MEDIA_ROOT
    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy_rerun.mp4")

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value=fake_proxy_path), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/target-versions/{tv_id}/run-analysis",
                                  json={"target_srt_path": str(srt_path)})
            assert r.status_code == 200
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.post(f"/target-versions/{tv_id}/rerun")
            assert r.status_code == 200
            assert r.json()["status"] == "analyzing"
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.status_code == 200
            assert r.json()["status"] == "review"


@pytest.mark.asyncio
async def test_rerun_returns_400_when_no_srt_path_stored():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="pending"); session.add(tv)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/target-versions/{tv_id}/rerun")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_target_version_exposes_pipeline_warnings(tmp_path, monkeypatch):
    """run_pipeline은 analyze_characters를 호출하지 않는다(인물/관계 로스터
    자체가 폐지됨). 이 테스트가 검증하려는 "파이프라인 어느 단계가 실패해도
    그 warning이 GET으로 노출되는가"를 계속 확인하려면 실제로 여전히
    호출되는 단계(문법 필요성 판단, 이제 spaCy 기반 파이썬 함수)를 실패시켜야
    한다."""
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

    def _check_grammar_necessity_raises(*args, **kwargs):
        raise RuntimeError("문법 필요성 판단 API 오류")

    monkeypatch.setattr(
        "app.core.pipeline.check_grammar_necessity", _check_grammar_necessity_raises)

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
    assert r.json()["warnings"] == [
        {"stage": "문법 필요성 판단", "message": "문법 필요성 판단 API 오류"}
    ]


@pytest.mark.asyncio
async def test_confirm_registers_rejects_when_segments_still_unresolved(tmp_path, monkeypatch):
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

    def _fake_grammar_necessity(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False,
                  "resolved_formality": None, "resolved_gender_from_korean": None,
                  "candidate_words": ["cansada"], "candidate_word_lemmas": ["cansado"],
                  "has_gender_hint": True}
                for p in pairs]

    monkeypatch.setattr("app.core.pipeline.check_grammar_necessity", _fake_grammar_necessity)

    # GET /target-versions/{id}가 video_proxy_url을 MEDIA_ROOT/video_proxy
    # 기준 상대경로로 계산하므로, 실제 generate_video_proxy가 만드는 것과
    # 같은 형태의 경로를 mock한다.
    from app.core.uploads import MEDIA_ROOT
    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy_reject.mp4")

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
            assert r.json()["status"] == "awaiting_confirmation"

            r = await client.post(f"/target-versions/{tv_id}/confirm-registers")
            assert r.status_code == 400

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.json()["status"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_confirm_registers_runs_ai_verification_after_all_resolved(tmp_path, monkeypatch):
    """성별 확인이 필요한 줄을 사람이(resolve-gender) 답한 뒤에만
    confirm-registers가 S2(AI 검증)를 실행해 status를 review로 넘겨야 한다."""
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

    def _fake_grammar_necessity(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False,
                  "resolved_formality": None, "resolved_gender_from_korean": None,
                  "candidate_words": ["cansada"], "candidate_word_lemmas": ["cansado"],
                  "has_gender_hint": True}
                for p in pairs]

    monkeypatch.setattr("app.core.pipeline.check_grammar_necessity", _fake_grammar_necessity)

    from app.core.uploads import MEDIA_ROOT
    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy_confirm.mp4")

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value=fake_proxy_path), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/target-versions/{tv_id}/run-analysis",
                              json={"target_srt_path": str(srt_path)})
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}/segments")
            seg_id = r.json()[0]["id"]
            r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"gender": "female"})
            assert r.status_code == 200

            r = await client.post(f"/target-versions/{tv_id}/confirm-registers")
            assert r.status_code == 200
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.json()["status"] == "review"

            r = await client.get(f"/target-versions/{tv_id}/findings")
            findings = r.json()
            assert any(f["category"] == "mistranslation" for f in findings)


@pytest.mark.asyncio
async def test_confirm_registers_saves_confirmed_character_gender_to_title(monkeypatch):
    """확인 화면에서 성별이 확정된(character_name이 있는) 그룹은
    confirm-registers 호출 시 character_gender_facts에 저장돼야 한다 —
    LLM의 1차 판정이 아니라 검수자가 최종 확인한 값(design 원칙 4)."""
    import asyncio
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async with async_session() as session:
        title = Title(name="Test Drama", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="awaiting_confirmation"); session.add(tv)
        await session.flush()
        seg = Segment(
            target_version_id=tv.id, index=0, start=0.0, end=1.0,
            korean_text="성경이 왔어.", target_text="Llegó Seong-gyeong.",
            gender_check_needed=True,
            resolved_gender_groups_raw=[{
                "group_index": 0, "referent": "특정 인물의 이름",
                "character_name": "성경", "words": ["Seong-gyeong"],
                "target_word_lemmas": ["seong-gyeong"], "candidate_indices": [0],
                "gender": "female", "suggested_gender": None, "human_confirmed": True,
            }],
        )
        session.add(seg)
        await session.commit()
        tv_id, title_id = tv.id, title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/target-versions/{tv_id}/confirm-registers")
        assert r.status_code == 200
        if app.state.background_tasks:
            await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title_id)
        assert facts == {"성경": "female"}


@pytest.mark.asyncio
async def test_confirm_registers_does_not_save_auto_resolved_unconfirmed_gender(monkeypatch):
    """has_gender_hint 규칙으로 자동 해소되어 사람이 한 번도 보지 못한
    그룹(human_confirmed 없음/False)은 gender 값이 있어도
    character_gender_facts에 저장되면 안 된다 — 사람이 확인 버튼을 누른
    적이 없기 때문(design 원칙 4, 최종 리뷰 지적 사항)."""
    import asyncio
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async with async_session() as session:
        title = Title(name="Test Drama 2", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="awaiting_confirmation"); session.add(tv)
        await session.flush()
        seg = Segment(
            target_version_id=tv.id, index=0, start=0.0, end=1.0,
            korean_text="성경이 왔어.", target_text="Llegó Seong-gyeong.",
            gender_check_needed=True,
            resolved_gender_groups_raw=[{
                "group_index": 0, "referent": "특정 인물의 이름",
                "character_name": "성경", "words": ["Seong-gyeong"],
                "target_word_lemmas": ["seong-gyeong"], "candidate_indices": [0],
                "gender": "female", "suggested_gender": None,
            }],
        )
        session.add(seg)
        await session.commit()
        tv_id, title_id = tv.id, title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/target-versions/{tv_id}/confirm-registers")
        assert r.status_code == 200
        if app.state.background_tasks:
            await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title_id)
        assert facts == {}


@pytest.mark.asyncio
async def test_get_target_version_includes_title_id(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="review"); session.add(tv)
        await session.commit()
        title_id, tv_id = title.id, tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}")
    assert r.status_code == 200
    assert r.json()["title_id"] == title_id
