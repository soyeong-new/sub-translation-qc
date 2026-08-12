import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_upload_video_saves_file_and_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("clip.mp4", b"fake video bytes", "video/mp4")}
        r = await client.post("/uploads/video", files=files)
        assert r.status_code == 200
        path = r.json()["path"]
        assert path.endswith("_clip.mp4")
        assert (tmp_path / "video").exists()


@pytest.mark.asyncio
async def test_upload_video_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("clip.exe", b"nope", "application/octet-stream")}
        r = await client.post("/uploads/video", files=files)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_srt_saves_file_and_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("sub.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHola\n", "text/plain")}
        r = await client.post("/uploads/srt", files=files)
        assert r.status_code == 200
        assert r.json()["path"].endswith("_sub.srt")


