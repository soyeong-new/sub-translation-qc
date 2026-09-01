import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


def _make_test_video(path: Path) -> None:
    """실제 ffmpeg으로 테스트용 mp4를 생성한다."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


async def _upload_chunk(client, data: bytes, filename: str = "clip.mp4", upload_id: str | None = None):
    return await client.post(
        "/uploads/video/chunk", content=data, headers={
            "X-Filename": filename,
            "X-Upload-Id": upload_id or str(uuid.uuid4()),
            "X-Chunk-Index": "0",
            "X-Total-Chunks": "1",
            "X-Total-Size": str(len(data)),
        },
    )


@pytest.mark.asyncio
async def test_upload_video_chunk_saves_file_and_returns_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await _upload_chunk(client, src.read_bytes())
        assert r.status_code == 200
        path = r.json()["path"]
        assert path.endswith("_clip.mp4")
        assert (tmp_path / "video").exists()


@pytest.mark.asyncio
async def test_upload_video_chunk_returns_status_until_last_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src)
    data = src.read_bytes()
    mid = len(data) // 2
    upload_id = str(uuid.uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            "/uploads/video/chunk", content=data[:mid], headers={
                "X-Filename": "clip.mp4", "X-Upload-Id": upload_id,
                "X-Chunk-Index": "0", "X-Total-Chunks": "2", "X-Total-Size": str(len(data)),
            },
        )
        assert r1.status_code == 200
        assert r1.json() == {"status": "chunk-received"}

        r2 = await client.post(
            "/uploads/video/chunk", content=data[mid:], headers={
                "X-Filename": "clip.mp4", "X-Upload-Id": upload_id,
                "X-Chunk-Index": "1", "X-Total-Chunks": "2", "X-Total-Size": str(len(data)),
            },
        )
        assert r2.status_code == 200
        assert r2.json()["path"].endswith("_clip.mp4")


@pytest.mark.asyncio
async def test_upload_video_chunk_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await _upload_chunk(client, b"nope", filename="clip.exe")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_video_chunk_rejects_missing_headers(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/uploads/video/chunk", content=b"nope")
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
    에러로 막아야 한다. 청크 엔드포인트는 청크 자체가 아니라 전체
    파일 크기(X-Total-Size) 기준으로 확인해야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    fake_usage = SimpleNamespace(total=10_000, used=9_900, free=100)
    monkeypatch.setattr("app.main.shutil.disk_usage", lambda path: fake_usage)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/uploads/video/chunk", content=b"x" * 50, headers={
                "X-Filename": "clip.mp4", "X-Upload-Id": str(uuid.uuid4()),
                "X-Chunk-Index": "0", "X-Total-Chunks": "1", "X-Total-Size": "5000",
            },
        )
    assert r.status_code == 507
    assert not (tmp_path / "video").exists()


@pytest.mark.asyncio
async def test_upload_allowed_when_disk_space_sufficient(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    fake_usage = SimpleNamespace(total=10 * 1024**3, used=1 * 1024**3, free=9 * 1024**3)
    monkeypatch.setattr("app.main.shutil.disk_usage", lambda path: fake_usage)
    src = tmp_path / "src.mp4"
    _make_test_video(src)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await _upload_chunk(client, src.read_bytes())
    assert r.status_code == 200
