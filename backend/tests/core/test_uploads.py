import pytest
from pathlib import Path
from app.core.uploads import (
    build_upload_destination, save_upload, UnsupportedFileType, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS,
)


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


def test_build_upload_destination_accepts_chart_image_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    dest = build_upload_destination("chart_image", "chart.png", IMAGE_EXTENSIONS)
    assert dest.parent == tmp_path / "chart_image"


def test_build_upload_destination_rejects_non_image_extension_for_chart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    with pytest.raises(UnsupportedFileType):
        build_upload_destination("chart_image", "chart.pdf", IMAGE_EXTENSIONS)
