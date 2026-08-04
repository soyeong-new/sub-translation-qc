import asyncio
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_attach_chart_image_starts_background_extraction(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    async def _fake_extract_chart_and_save(title_id, image_path):
        async with async_session() as session:
            t = await session.get(Title, title_id)
            t.chart_extraction_status = "review_needed"
            await session.commit()

    transport = ASGITransport(app=app)
    with patch("app.main.extract_chart_and_save", side_effect=_fake_extract_chart_and_save):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/titles/{title_id}/chart-image",
                                  json={"image_path": "/fake/chart.png"})
            assert r.status_code == 200
            assert r.json()["status"] == "processing"

            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

    async with async_session() as session:
        title = await session.get(Title, title_id)
        assert title.chart_image_path == "/fake/chart.png"
        assert title.chart_extraction_status == "review_needed"


@pytest.mark.asyncio
async def test_attach_chart_image_returns_404_for_missing_title(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/titles/does-not-exist/chart-image",
                              json={"image_path": "/fake/chart.png"})
    assert r.status_code == 404
