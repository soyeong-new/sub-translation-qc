import subprocess
import pytest
from pathlib import Path
from app.core.uploads import (
    build_upload_destination, save_upload, save_video_upload_streamed,
    UnsupportedFileType, VIDEO_EXTENSIONS,
)


def _make_test_video(path: Path, duration: int, size: str, faststart: bool) -> None:
    """실제 ffmpeg으로 테스트용 mp4를 생성한다 — subprocess를 모킹하면
    "파이프 입력에서 seek 불가능해 실패한다"는 실제 ffmpeg 동작 자체를
    검증할 수 없어(이 버그를 처음 찾을 때도 실제 ffmpeg으로만 재현됐다),
    이 파일만은 예외적으로 진짜 ffmpeg을 돌린다."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
async def test_save_video_upload_streamed_transcodes_faststart_video(tmp_path, monkeypatch):
    """design 2026-08-31: 원본을 통째로 복사하지 않고, 받으면서 바로
    ffmpeg에 흘려보내 압축된 결과만 저장해야 한다 — faststart(moov가
    앞쪽) 입력이면 파이프로도 정상 처리된다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=1, size="320x240", faststart=True)

    path = await save_video_upload_streamed("clip.mp4", _bytes_stream(src.read_bytes()))

    out = Path(path)
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.parent == tmp_path / "video"


@pytest.mark.asyncio
async def test_save_video_upload_streamed_fails_and_cleans_up_for_non_faststart_video(tmp_path, monkeypatch):
    """실측: moov 원자가 파일 끝에 있는(faststart 아닌) 영상은 어느 정도
    크기부터 파이프 입력에서 ffmpeg이 스트림 정보를 못 찾아 실패한다.
    이때 쓰다 만(또는 0바이트) 출력 파일이 남으면 안 된다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=20, size="640x480", faststart=False)

    with pytest.raises(RuntimeError, match="faststart"):
        await save_video_upload_streamed("clip.mp4", _bytes_stream(src.read_bytes()))

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []
