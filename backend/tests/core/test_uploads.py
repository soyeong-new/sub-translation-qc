import asyncio
import subprocess
import pytest
from pathlib import Path
from app.core.uploads import (
    build_upload_destination, save_upload, start_video_upload, get_video_upload_status,
    cleanup_orphaned_upload_temp_files, UnsupportedFileType, VIDEO_EXTENSIONS,
)


async def _wait_for_compression(upload_id: str, timeout: float = 30.0) -> dict:
    elapsed = 0.0
    while elapsed < timeout:
        status = get_video_upload_status(upload_id)
        if status["status"] != "compressing":
            return status
        await asyncio.sleep(0.1)
        elapsed += 0.1
    raise TimeoutError(f"압축이 {timeout}초 안에 안 끝남")


def _make_test_video(path: Path, duration: int, size: str) -> None:
    """실제 ffmpeg으로 테스트용 mp4를 생성한다."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(path),
    ]
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
async def test_start_video_upload_saves_then_compresses_in_background(tmp_path, monkeypatch):
    """원본을 먼저 디스크에 그대로 받아 적으면 압축을 기다리지 않고 곧장
    upload_id를 돌려준다. 압축이 끝날 때까지 응답을 물고 있으면, 압축이
    오래 걸리는 동안(t3.micro) 요청이 데이터 없이 계속 열려있게 되고, 그
    무응답 구간에서 중간 네트워크 장비가 연결을 끊어버렸다(실측,
    ERR_NETWORK_CHANGED, 2026-09-02) — 그래서 압축은 백그라운드로 넘기고
    완료 여부는 폴링으로 따로 확인한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=20, size="640x480")

    upload_id = await start_video_upload("clip.mp4", _bytes_stream(src.read_bytes()))
    assert get_video_upload_status(upload_id)["status"] == "compressing"

    status = await _wait_for_compression(upload_id)

    assert status["status"] == "done"
    out = Path(status["path"])
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.parent == tmp_path / "video"
    assert out.stat().st_size < src.stat().st_size
    assert list(tmp_path.glob("video/.in_*")) == []
    assert list(tmp_path.glob("video/.out_*")) == []
    # done으로 한 번 읽었으니 레지스트리에서 지워져야 한다
    with pytest.raises(KeyError):
        get_video_upload_status(upload_id)


@pytest.mark.asyncio
async def test_start_video_upload_reports_failure_for_corrupt_input(tmp_path, monkeypatch):
    """진짜 깨진(영상이 아닌) 입력은 압축 단계에서 실패해야 하고, 이때
    status가 "failed"로 보고되며 최종 경로에도 임시 파일에도 아무것도
    안 남아야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)

    upload_id = await start_video_upload("clip.mp4", _bytes_stream(b"not a real video" * 1000))
    status = await _wait_for_compression(upload_id)

    assert status["status"] == "failed"
    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []


def test_get_video_upload_status_raises_for_unknown_id():
    with pytest.raises(KeyError):
        get_video_upload_status("no-such-upload")


def test_cleanup_orphaned_upload_temp_files_deletes_in_and_out_files(tmp_path, monkeypatch):
    """실측: ffmpeg 압축 도중 서버 프로세스가 죽으면(재시작, OOM 등) .in_과
    .out_ 임시 파일이 둘 다 정리되지 못한 채 남았다. 서버 시작 시점엔
    진행 중인 업로드가 있을 수 없으므로 이 시점에 남은 임시 파일은 전부
    주인이 없다 — 재시작할 때마다 쓸어야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    (video_dir / ".in_abc_clip.mp4").write_bytes(b"partial")
    (video_dir / ".out_abc_clip.mp4").write_bytes(b"partial")
    (video_dir / "done_clip.mp4").write_bytes(b"real file")  # 정상 파일은 건드리면 안 됨

    cleanup_orphaned_upload_temp_files()

    remaining = {p.name for p in video_dir.glob("*")}
    assert remaining == {"done_clip.mp4"}
