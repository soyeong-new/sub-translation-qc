import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion, Character, Relationship, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_list_segments_for_target_version():
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        session.add(Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                            korean_text="안녕", target_text="Hola"))
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/segments")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["korean_text"] == "안녕"


@pytest.mark.asyncio
async def test_list_characters_for_target_version():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        session.add(Character(title_id=title.id, label="테스트인물"))
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/characters")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["label"] == "테스트인물"
        assert body[0]["confirmed_gender"] is None


@pytest.mark.asyncio
async def test_list_characters_scoped_by_title_shared_across_episodes():
    # Characters are keyed by title_id, not target_version_id (design §: 인물은 작품
    # 단위 전역 공유). A character created against one episode of a title must still
    # show up when listed via a *different* episode/target-version of the same title.
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        ep1 = Episode(title_id=title.id, video_path="/1.mp4")
        ep2 = Episode(title_id=title.id, video_path="/2.mp4")
        session.add_all([ep1, ep2]); await session.flush()
        tv1 = TargetVersion(episode_id=ep1.id, target_language="es", variant="LATAM")
        tv2 = TargetVersion(episode_id=ep2.id, target_language="es", variant="LATAM")
        session.add_all([tv1, tv2]); await session.flush()
        session.add(Character(title_id=title.id, label="공유인물"))
        await session.commit()
        tv2_id = tv2.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv2_id}/characters")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["label"] == "공유인물"


@pytest.mark.asyncio
async def test_list_characters_404_for_missing_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/characters")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_relationships_for_target_version_includes_character_labels():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        a = Character(title_id=title.id, label="화자")
        b = Character(title_id=title.id, label="상대")
        session.add_all([a, b]); await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id,
                           addressee_character_id=b.id)
        session.add(rel)
        await session.commit()
        tv_id = tv.id
        rel_id = rel.id
        a_id, b_id = a.id, b.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/relationships")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        rel_json = body[0]
        assert rel_json["id"] == rel_id
        assert rel_json["speaker_character_id"] == a_id
        assert rel_json["addressee_character_id"] == b_id
        assert rel_json["speaker_label"] == "화자"
        assert rel_json["addressee_label"] == "상대"
        assert rel_json["confirmed_formality_level"] is None


@pytest.mark.asyncio
async def test_list_relationships_404_for_missing_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/relationships")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_characters_are_populated_after_a_real_run_analysis(tmp_path, monkeypatch):
    """Task 5(290b60d) 이후로 run_pipeline은 더 이상 스스로 인물을 추론하지
    않는다 — 인물/관계 로스터는 title에 이미 저장된 Character/Relationship에서
    그대로 읽어와(prior_characters/prior_relationships) 넘겨받는다. 그래서 이
    테스트는 "LLM이 새로 인물을 발견하는지"가 아니라, title에 미리 등록해 둔
    인물이 실제 run-analysis HTTP 흐름을 거쳐도 그대로 유지되고 GET
    /characters로 조회되는지를 확인한다."""
    from unittest.mock import patch

    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHola aquí\n", encoding="utf-8")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        character = Character(title_id=title.id, label="인물1")
        session.add(character)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/target-versions/{tv_id}/run-analysis",
                                  json={"target_srt_path": str(srt_path)})
            assert r.status_code == 200

            # background task들이 완료될 때까지 대기한다.
            # 패치가 활성 상태인 동안 waiting하므로, background task가
            # extract_audio를 호출할 때 여전히 mock이 활성이다.
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}/characters")
            assert r.status_code == 200
            chars = r.json()

    assert chars, "run-analysis 이후 인물 목록이 비어 있으면 안 된다"
    assert chars[0]["label"] == "인물1"
    assert chars[0]["confirmed_gender"] is None  # 확인 대기 상태


async def _tv_with_episode(session) -> str:
    title = Title(name="T", type="movie"); session.add(title); await session.flush()
    episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
    tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
    session.add(tv)
    await session.commit()
    return tv.id


@pytest.mark.parametrize("endpoint", ["characters", "relationships"])
@pytest.mark.asyncio
async def test_lookup_returns_404_when_episode_missing(endpoint):
    """episode 조회 결과를 None 체크 없이 쓰면 episode.title_id 접근에서
    AttributeError(500)가 난다. run-analysis와 동일하게 404로 나가야 한다.

    target_versions.episode_id에는 FK가 걸려 있어 DB 상태만으로는 이 상황을
    만들 수 없으므로, Episode 조회만 None을 돌려주도록 가로챈다."""
    from unittest.mock import patch
    from sqlalchemy.ext.asyncio import AsyncSession

    async with async_session() as session:
        tv_id = await _tv_with_episode(session)

    real_get = AsyncSession.get

    async def get_with_missing_episode(self, entity, ident, *args, **kwargs):
        if entity is Episode:
            return None
        return await real_get(self, entity, ident, *args, **kwargs)

    transport = ASGITransport(app=app)
    with patch.object(AsyncSession, "get", get_with_missing_episode):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/target-versions/{tv_id}/{endpoint}")

    assert r.status_code == 404
    assert r.json()["detail"] == "episode not found"


@pytest.mark.asyncio
async def test_get_target_version_returns_status_and_error_message():
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status="failed", error_message="Gemini API 오류: timeout")
        session.add(tv)
        await session.commit()
        tv_id = tv.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Gemini API 오류: timeout"


@pytest.mark.asyncio
async def test_get_target_version_404_when_missing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/nonexistent")
    assert r.status_code == 404
