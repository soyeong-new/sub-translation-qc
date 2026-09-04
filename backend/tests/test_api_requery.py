import pytest
from unittest.mock import AsyncMock, patch
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


async def _make_finding(model: str) -> str:
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안녕", target_text="hola")
        session.add(seg); await session.flush()
        f = FindingRow(id="f1", target_version_id=tv.id, segment_id=seg.id,
                       category="mistranslation", description="근거",
                       original_text="hola", suggested_text="hola corregido",
                       confidence=0.9, model=model, status="pending")
        session.add(f)
        await session.commit()
        return f.id


@pytest.mark.asyncio
async def test_requery_finding_updates_suggested_text_and_resets_to_pending(monkeypatch):
    """model="claude" finding의 재질문은 claude(correct_primary)에게 간다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="claude")

    with patch("app.providers.mock.MockProvider.correct_primary",
               new=AsyncMock(return_value=[{"segment_id": "seg1", "category": "mistranslation",
                                             "corrected_text": "hola más formal",
                                             "description": "재질문 반영"}])):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/findings/{finding_id}/requery",
                                   json={"instruction": "더 격식있게", "reviewer_name": "김검수"})

    assert r.status_code == 200
    body = r.json()
    assert body["suggested_text"] == "hola más formal"
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_requery_refreshes_back_translation_replacing_stale_tag(monkeypatch):
    """diagnosis: 재질문 후 suggested_text는 새 교정문으로 바뀌는데, 검수자가
    보는 역번역("한국어 역번역 참고" 태그)은 예전 제안 기준 그대로 남아있었다
    — 이제는 같은 응답의 back_translation으로 그 태그만 갱신해야 하고,
    original_text에 대한 "원본 한국어 역번역 참고" 태그는 재질문과 무관하니
    그대로 보존해야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="claude")
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        finding.description = (
            "근거 (한국어 역번역 참고: 예전 역번역) (원본 한국어 역번역 참고: 원본 역번역)"
        )
        await session.commit()

    with patch("app.providers.mock.MockProvider.correct_primary",
               new=AsyncMock(return_value=[{"segment_id": "seg1", "category": "mistranslation",
                                             "corrected_text": "hola más formal",
                                             "description": "재질문 반영",
                                             "back_translation": "새 역번역"}])):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/findings/{finding_id}/requery",
                                   json={"instruction": "더 격식있게", "reviewer_name": "김검수"})

    assert r.status_code == 200
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
    assert "새 역번역" in finding.description
    assert "예전 역번역" not in finding.description
    assert "원본 역번역" in finding.description


@pytest.mark.asyncio
async def test_requery_reapplies_already_confirmed_gender_to_new_suggestion(monkeypatch):
    """회귀: 재질문으로 AI가 문장을 다시 쓰면서 이미 확정된 성별을 무시하고
    다른 성별로 써버릴 수 있다 — S2 이중검증과 같은 문제라 같은 방식(재적용)
    으로 막아야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="나 피곤해", target_text="Estoy cansada.",
                      gender_check_needed=True, resolved_gender_raw="female")
        session.add(seg); await session.flush()
        f = FindingRow(id="f1", target_version_id=tv.id, segment_id=seg.id,
                       category="mistranslation", description="근거",
                       original_text="Estoy cansada.", suggested_text="hola corregido",
                       confidence=0.9, model="claude", status="pending")
        session.add(f)
        await session.commit()
        finding_id = f.id

    with patch("app.providers.mock.MockProvider.correct_primary",
               new=AsyncMock(return_value=[
                   {"segment_id": "seg1", "category": "mistranslation",
                    "corrected_text": "Sí, ahora veo que estás muy cansado.",
                    "description": "재질문 반영"}])):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/findings/{finding_id}/requery",
                                   json={"instruction": "더 자연스럽게", "reviewer_name": "김검수"})

    assert r.status_code == 200
    assert r.json()["suggested_text"] == "Sí, ahora veo que estás muy cansada."


@pytest.mark.asyncio
async def test_requery_shrinks_suggestion_that_exceeds_line_length(monkeypatch):
    """회귀(사용자 재현): 프롬프트에 "줄당 50자 이내" 지시가 있어도 LLM이
    다시 질문 응답에서 이를 안 지킬 수 있다 — 다시 질문 결과는 곧바로
    pending으로 저장되고 검수자가 승인하기 전까지 다른 안전망을 안 거치므로,
    여기서 미리 강제해야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="claude")
    long_text = ("Esta es una oracion muy larga que definitivamente supera "
                 "los cincuenta caracteres permitidos")

    with patch("app.providers.mock.MockProvider.correct_primary",
               new=AsyncMock(return_value=[{"segment_id": "seg1", "category": "mistranslation",
                                             "corrected_text": long_text,
                                             "description": "재질문 반영"}])):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/findings/{finding_id}/requery",
                                   json={"instruction": "더 격식있게", "reviewer_name": "김검수"})

    assert r.status_code == 200
    suggested_text = r.json()["suggested_text"]
    assert suggested_text != long_text
    assert all(len(ln) <= 50 for ln in suggested_text.split("\n"))


@pytest.mark.asyncio
async def test_requery_returns_400_when_provider_returns_no_results(monkeypatch):
    """회귀: LLM이 빈 배열을 돌려주면(재질문 지시와 배치용 스킵 지시가 충돌한
    경우) 예전에는 200과 함께 원문 그대로를 조용히 돌려줘서, 검수자는 재질문이
    실제로 반영됐는지 알 수 없었다 — 이제는 400으로 명시적으로 실패를
    알려야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="claude")

    with patch("app.providers.mock.MockProvider.correct_primary",
               new=AsyncMock(return_value=[])):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/findings/{finding_id}/requery",
                                   json={"instruction": "더 격식있게", "reviewer_name": "김검수"})

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_requery_rejects_rule_based_finding(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="사전필터")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/findings/{finding_id}/requery",
                               json={"instruction": "다시 봐줘", "reviewer_name": "김검수"})

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_requery_returns_404_for_unknown_finding():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/findings/does-not-exist/requery",
                               json={"instruction": "x", "reviewer_name": "y"})
    assert r.status_code == 404
