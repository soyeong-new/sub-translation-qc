import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_list_language_profiles_returns_es_latam():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/language-profiles")
    assert r.status_code == 200
    assert any(p["language"] == "es" and p["variant"] == "LATAM" for p in r.json())
