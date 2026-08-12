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
    await engine.dispose()


async def _make_segment(**overrides) -> tuple[str, str]:
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                      korean_text="안녕", target_text="hola", **overrides)
        session.add(seg)
        await session.commit()
        return tv.id, seg.id


@pytest.mark.asyncio
async def test_list_flagged_segments_returns_only_flagged_ones():
    tv_id, flagged_id = await _make_segment(gender_check_needed=True)
    async with async_session() as session:
        unflagged = Segment(target_version_id=tv_id, index=1, start=2.0, end=3.0,
                            korean_text="뭐해", target_text="que haces")
        session.add(unflagged)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == flagged_id
    assert body[0]["gender_check_needed"] is True


@pytest.mark.asyncio
async def test_resolve_gender_with_raw_value():
    tv_id, seg_id = await _make_segment(gender_check_needed=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"gender": "female"})
    assert r.status_code == 200
    assert r.json()["resolved_gender_raw"] == "female"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_gender_raw == "female"


@pytest.mark.asyncio
async def test_list_flagged_segments_includes_resolved_gender_groups_raw():
    """한 줄에 인물이 둘 이상이면 flagged-segments 응답에
    resolved_gender_groups_raw가 그대로 실려야 프론트가 인물별로 따로
    질문을 그릴 수 있다."""
    groups = [
        {"words": ["cansado"], "target_word_lemmas": ["cansado"], "gender": None},
        {"words": ["enojado"], "target_word_lemmas": ["enojado"], "gender": None},
    ]
    tv_id, seg_id = await _make_segment(
        gender_check_needed=True, resolved_gender_groups_raw=groups)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
    assert r.status_code == 200
    assert r.json()[0]["resolved_gender_groups_raw"] == groups


@pytest.mark.asyncio
async def test_resolve_gender_group_updates_only_the_targeted_group():
    """다인물 줄에서 group_index로 지정한 인물의 답만 바뀌고, 다른 인물의
    답은(이미 확정돼 있었다면) 건드리지 않아야 한다."""
    groups = [
        {"words": ["cansado"], "target_word_lemmas": ["cansado"], "gender": None},
        {"words": ["enojado"], "target_word_lemmas": ["enojado"], "gender": "male"},
    ]
    tv_id, seg_id = await _make_segment(
        gender_check_needed=True, resolved_gender_groups_raw=groups)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/resolve-gender-group",
            json={"group_index": 0, "gender": "female"})
    assert r.status_code == 200
    updated = r.json()["resolved_gender_groups_raw"]
    assert updated[0]["gender"] == "female"
    assert updated[1]["gender"] == "male"  # 다른 인물의 답은 그대로

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_gender_groups_raw[0]["gender"] == "female"
        assert seg.resolved_gender_groups_raw[1]["gender"] == "male"


@pytest.mark.asyncio
async def test_resolve_gender_reapplies_to_pending_finding_suggested_text():
    """회귀: STT 재검증이 성별 확인을 기다리며 만든 pending finding이
    있으면, 사람이 그 자리에서 답한 성별이 finding의 제안문구에도 곧바로
    반영돼야 한다 — 안 그러면 카드에는 여전히 "cansado/a" 같은 미확정
    표기가 남아있게 된다."""
    tv_id, seg_id = await _make_segment(gender_check_needed=True)
    async with async_session() as session:
        finding = FindingRow(
            id="f1", target_version_id=tv_id, segment_id=seg_id,
            category="mistranslation", description="테스트",
            original_text="hola", suggested_text="Te ves cansado/a.",
            confidence=1.0, source="llm", model="gpt", status="pending",
        )
        session.add(finding)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-gender", json={"gender": "female"})
    assert r.status_code == 200

    async with async_session() as session:
        finding = await session.get(FindingRow, "f1")
        assert finding.suggested_text == "Te ves cansada."


@pytest.mark.asyncio
async def test_resolve_gender_group_rejects_out_of_range_index():
    groups = [{"words": ["cansado"], "target_word_lemmas": ["cansado"], "gender": None}]
    tv_id, seg_id = await _make_segment(
        gender_check_needed=True, resolved_gender_groups_raw=groups)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/resolve-gender-group",
            json={"group_index": 5, "gender": "female"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_resolve_formality_with_raw_value():
    tv_id, seg_id = await _make_segment(formality_check_needed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/segments/{seg_id}/resolve-formality",
                              json={"formality_level": "formal"})
    assert r.status_code == 200
    assert r.json()["resolved_formality_raw"] == "formal"

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.resolved_formality_raw == "formal"


@pytest.mark.asyncio
async def test_list_flagged_segments_returns_404_for_nonexistent_target_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/target-versions/does-not-exist/flagged-segments")
    assert r.status_code == 404
