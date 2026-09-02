from pathlib import Path
from types import SimpleNamespace
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_upload_video_saves_file_and_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/uploads/video", content=b"fake video bytes", headers={"X-Filename": "clip.mp4"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["path"].endswith("_clip.mp4")
        assert Path(body["path"]).read_bytes() == b"fake video bytes"


@pytest.mark.asyncio
async def test_upload_video_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/uploads/video", content=b"nope", headers={"X-Filename": "clip.exe"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_video_rejects_missing_filename_header(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/uploads/video", content=b"nope")
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


@pytest.mark.asyncio
async def test_upload_rejected_when_disk_space_insufficient(tmp_path, monkeypatch):
    """실측(프로덕션): 9GB 영상을 여유 공간 7GB인 서버에 올렸더니, 우리
    코드에 도달하기도 전에 Starlette가 본문을 파싱하는 중 디스크가 꽉 차
    "There was an error parsing the body"라는 알아보기 힘든 영어 에러로
    실패했다. 라우팅 전에(미들웨어에서) 여유 공간을 미리 확인해 명확한
    에러로 막아야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    fake_usage = SimpleNamespace(total=10_000, used=9_900, free=100)
    monkeypatch.setattr("app.main.shutil.disk_usage", lambda path: fake_usage)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/uploads/video", content=b"x" * 5000, headers={"X-Filename": "clip.mp4"},
        )
    assert r.status_code == 507


@pytest.mark.asyncio
async def test_upload_allowed_when_disk_space_sufficient(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    fake_usage = SimpleNamespace(total=10 * 1024**3, used=1 * 1024**3, free=9 * 1024**3)
    monkeypatch.setattr("app.main.shutil.disk_usage", lambda path: fake_usage)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/uploads/video", content=b"fake video bytes", headers={"X-Filename": "clip.mp4"},
        )
    assert r.status_code == 200
