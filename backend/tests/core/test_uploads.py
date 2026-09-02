import pytest
from pathlib import Path
from app.core.uploads import (
    build_upload_destination, save_upload, save_video_upload, UnsupportedFileType, VIDEO_EXTENSIONS,
)


async def _bytes_stream(data: bytes, chunk_size: int = 65536):
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]


def test_build_upload_destination_strips_directory_components(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    dest = build_upload_destination("video", "../../etc/passwd.mp4", VIDEO_EXTENSIONS)
    assert dest.parent == tmp_path / "video"
    assert dest.name.endswith("_passwd.mp4")
    assert ".." not in str(dest)


def test_build_upload_destination_rejects_disallowed_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    with pytest.raises(UnsupportedFileType):
        build_upload_destination("video", "movie.exe", VIDEO_EXTENSIONS)


def test_build_upload_destination_adds_unique_prefix_for_same_filename(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    dest1 = build_upload_destination("video", "clip.mp4", VIDEO_EXTENSIONS)
    dest2 = build_upload_destination("video", "clip.mp4", VIDEO_EXTENSIONS)
    assert dest1 != dest2


@pytest.mark.asyncio
async def test_save_upload_streams_all_chunks_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    chunks = [b"hello ", b"world", b""]

    async def fake_read(size):
        return chunks.pop(0)

    path = await save_upload("srt", "test.srt", fake_read, {".srt"})
    assert Path(path).read_bytes() == b"hello world"
    assert Path(path).parent == tmp_path / "srt"


@pytest.mark.asyncio
async def test_save_upload_deletes_partial_file_when_write_fails(tmp_path, monkeypatch):
    """실측(프로덕션): 디스크가 꽉 차 업로드 도중 write가 실패하면, 쓰다 만
    파일이 지워지지 않고 그대로 남아 다음 시도 때 오히려 공간을 더
    까먹었다. 쓰기 실패 시 부분 파일을 자동으로 지워야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    chunks = [b"first chunk ", RuntimeError("디스크 꽉 참(가상)")]

    async def fake_read(size):
        item = chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with pytest.raises(RuntimeError):
        await save_upload("video", "clip.mp4", fake_read, VIDEO_EXTENSIONS)

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []


@pytest.mark.asyncio
async def test_save_video_upload_saves_file_without_compressing(tmp_path, monkeypatch):
    """업로드 시점 압축을 없앴다(design 2026-09-02) — 원본 바이트가 그대로
    저장돼야 하고, 압축이 없으니 결과 파일 크기가 입력과 정확히 같아야
    한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    data = b"fake video bytes" * 1000

    path = await save_video_upload("clip.mp4", _bytes_stream(data))

    out = Path(path)
    assert out.exists()
    assert out.read_bytes() == data
    assert out.parent == tmp_path / "video"


@pytest.mark.asyncio
async def test_save_video_upload_deletes_partial_file_when_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)

    async def failing_stream():
        yield b"first chunk"
        raise RuntimeError("네트워크 끊김(가상)")

    with pytest.raises(RuntimeError):
        await save_video_upload("clip.mp4", failing_stream())

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []
