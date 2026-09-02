import subprocess
import uuid
import pytest
from pathlib import Path
from app.core.uploads import (
    build_upload_destination, save_upload, save_video_chunk, abandon_video_chunk_upload,
    cleanup_orphaned_upload_temp_files, UnsupportedFileType, VIDEO_EXTENSIONS,
)


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


def _chunks(data: bytes, chunk_size: int) -> list:
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]


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
async def test_save_video_chunk_assembles_then_compresses(tmp_path, monkeypatch):
    """design 2026-09-02: 청크를 순서대로 이어 받다가, 마지막 청크에서
    합쳐진 원본을 ffmpeg으로 압축한다. 중간 청크들은 아직 안 끝났다는
    뜻으로 None을 반환하고, 마지막 청크에서만 최종 경로를 반환한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=20, size="640x480")
    chunks = _chunks(src.read_bytes(), chunk_size=200_000)
    upload_id = str(uuid.uuid4())

    path = None
    for i, chunk in enumerate(chunks):
        path = await save_video_chunk(upload_id, i, len(chunks), "clip.mp4", chunk)
        if i < len(chunks) - 1:
            assert path is None

    out = Path(path)
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.parent == tmp_path / "video"
    assert out.stat().st_size < src.stat().st_size
    assert list(tmp_path.glob("video/.in_*")) == []
    assert list(tmp_path.glob("video/.out_*")) == []


@pytest.mark.asyncio
async def test_save_video_chunk_ignores_retried_chunk(tmp_path, monkeypatch):
    """실측 시나리오: 청크를 보내고 서버는 저장했는데 응답이 클라이언트에
    안 닿으면(네트워크 순간 끊김), 클라이언트는 같은 청크 번호를 다시
    보낸다 — 이걸 그대로 이어 쓰면 파일이 중복 데이터로 깨지므로 무시해야
    한다. 재전송된 게 마지막 청크라면(이미 압축까지 끝났다면), 다시
    이어 쓰지는 않되 응답을 못 받은 클라이언트를 위해 같은 최종 경로를
    돌려줘야 한다 — 새 파일을 또 만들거나 재압축하면 안 된다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=1, size="320x240")
    data = src.read_bytes()
    chunks = _chunks(data, chunk_size=len(data))  # 한 청크
    upload_id = str(uuid.uuid4())

    result1 = await save_video_chunk(upload_id, 0, 1, "clip.mp4", chunks[0])
    result2 = await save_video_chunk(upload_id, 0, 1, "clip.mp4", chunks[0])  # 재시도

    assert result1 is not None  # 마지막(유일한) 청크라 바로 압축까지 끝남
    assert result2 == result1  # 같은 최종 경로를 다시 돌려줘야 한다(재압축 X)
    assert len(list((tmp_path / "video").glob("*.mp4"))) == 1  # 중복 파일 없음


@pytest.mark.asyncio
async def test_abandon_video_chunk_upload_deletes_partial_file(tmp_path, monkeypatch):
    """실측: 청크 재전송까지 다 실패해 프론트가 업로드를 포기해도, 서버는
    그 사실을 알 방법이 없어 이어붙이던 임시 원본 파일이 디스크에 그대로
    남았다. 프론트가 포기를 알리면 세션과 임시 파일을 지워야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    upload_id = str(uuid.uuid4())
    await save_video_chunk(upload_id, 0, 3, "clip.mp4", b"first")  # 마지막 청크 전 — 임시 파일만 있음

    abandon_video_chunk_upload(upload_id)

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []


def test_cleanup_orphaned_upload_temp_files_deletes_in_and_out_files(tmp_path, monkeypatch):
    """실측: ffmpeg 압축 도중 서버 프로세스가 죽으면(재시작, OOM 등) .in_과
    .out_ 임시 파일이 둘 다 정리되지 못한 채 남았다. 서버 시작 시점엔
    _UPLOAD_SESSIONS가 항상 비어있으므로 이 시점에 남은 임시 파일은 전부
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


@pytest.mark.asyncio
async def test_save_video_chunk_rejects_out_of_order_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    upload_id = str(uuid.uuid4())
    await save_video_chunk(upload_id, 0, 3, "clip.mp4", b"first")

    with pytest.raises(RuntimeError):
        await save_video_chunk(upload_id, 2, 3, "clip.mp4", b"third")  # 1번을 건너뜀

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []


@pytest.mark.asyncio
async def test_save_video_chunk_cleans_up_for_corrupt_input(tmp_path, monkeypatch):
    """진짜 깨진(영상이 아닌) 입력은 마지막 청크에서 압축이 실패해야 하고,
    이때 최종 경로에도 임시 파일에도 아무것도 안 남아야 한다."""
    monkeypatch.setattr("app.core.uploads.MEDIA_ROOT", tmp_path)
    upload_id = str(uuid.uuid4())

    with pytest.raises(RuntimeError):
        await save_video_chunk(upload_id, 0, 1, "clip.mp4", b"not a real video" * 1000)

    leftover = list((tmp_path / "video").glob("*")) if (tmp_path / "video").exists() else []
    assert leftover == []
