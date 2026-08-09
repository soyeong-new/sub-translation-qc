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
    """finding.model이 claude든 gpt든 claude+gpt든, 재질문은 GPT
    (verify_and_refine) 단일 모델만 쓴다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    finding_id = await _make_finding(model="claude")

    with patch("app.providers.mock.MockProvider.verify_and_refine",
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
