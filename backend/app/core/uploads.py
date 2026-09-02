"""업로드된 파일을 경로 조작 없이 로컬 디스크에 스트리밍 저장하는 모듈."""

import asyncio
import uuid
from pathlib import Path
from typing import Awaitable, AsyncIterator, Callable, Set

MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
SRT_EXTENSIONS = {".srt"}

_CHUNK_SIZE = 1024 * 1024  # 1MB


class UnsupportedFileType(ValueError):
    pass


def build_upload_destination(kind: str, filename: str, allowed_extensions: Set[str]) -> Path:
    """업로드 저장 경로를 계산한다. filename은 Path(...).name으로 경로 구분자를
    제거해 경로 조작을 막고(load_profile()의 경로 검증과 같은 원칙), uuid 접두어로
    같은 파일명이 와도 충돌하지 않게 한다. 디스크에 실제로 쓰지는 않는다 —
    호출자가 스트리밍으로 쓴다."""
    safe_name = Path(filename).name
    ext = Path(safe_name).suffix.lower()
    if ext not in allowed_extensions:
        raise UnsupportedFileType(f"허용되지 않는 확장자입니다: {ext}")
    dest_dir = MEDIA_ROOT / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / f"{uuid.uuid4()}_{safe_name}"


async def save_upload(
    kind: str,
    filename: str,
    read_chunk: Callable[[int], Awaitable[bytes]],
    allowed_extensions: Set[str],
) -> str:
    """read_chunk(size)는 다 읽으면 b''을 반환하는 awaitable 콜러블이어야 한다
    (FastAPI UploadFile.read와 동일한 시그니처). 메모리에 전체를 올리지 않고
    청크 단위로 디스크에 스트리밍해 수백 MB~수 GB 영상 파일도 안전하게 저장한다."""
    dest = build_upload_destination(kind, filename, allowed_extensions)
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await read_chunk(_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
    except Exception:
        # 실측(프로덕션): 디스크가 꽉 차 쓰기 도중 실패하면 쓰다 만 파일이
        # 그대로 남아, 다음 시도 때 오히려 공간을 더 까먹었다. 실패 시
        # 부분 파일을 지워야 재시도할 여유 공간이 확보된다.
        Path(dest).unlink(missing_ok=True)
        raise
    return str(dest)


_FFMPEG_VIDEO_ARGS = [
    "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy",
]


async def _run_ffmpeg(args: list) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"영상 변환 실패: {stderr.decode(errors='replace')[-500:]}")


# upload_id -> 압축 백그라운드 태스크. 프로세스 메모리에만 있어 서버가
# 재시작되면 사라진다 — 그 경우 폴링은 404를 받고 사용자에게 재업로드를
# 안내하면 되고, 남은 임시 파일은 cleanup_orphaned_upload_temp_files()가
# 다음 시작 시 정리한다.
_COMPRESS_TASKS: dict[str, asyncio.Task] = {}


async def _compress_video(in_tmp: Path, dest: Path) -> str:
    out_tmp = dest.with_name(f".out_{dest.name}")
    try:
        await _run_ffmpeg(["ffmpeg", "-y", "-i", str(in_tmp), *_FFMPEG_VIDEO_ARGS, str(out_tmp)])
        Path(out_tmp).replace(dest)
    except Exception:
        Path(out_tmp).unlink(missing_ok=True)
        raise
    finally:
        Path(in_tmp).unlink(missing_ok=True)
    return str(dest)


async def start_video_upload(filename: str, body_stream: AsyncIterator[bytes]) -> str:
    """원본을 디스크에 다 받아 적으면 곧장 upload_id를 돌려주고, ffmpeg
    압축은 백그라운드 태스크로 넘긴다(design 2026-09-02, 실측: 압축이 끝날
    때까지 응답을 물고 있으면 t3.micro에서 수분~수십분씩 걸리는 그 무응답
    구간 동안 중간 네트워크 장비가 연결을 죽은 것으로 보고 끊어버렸다 —
    ERR_NETWORK_CHANGED). 압축 완료 여부는 get_video_upload_status()를
    짧은 주기로 폴링해서 확인한다 — 이 요청들은 매번 금방 끝나 같은
    문제가 없다."""
    dest = build_upload_destination("video", filename, VIDEO_EXTENSIONS)
    in_tmp = dest.with_name(f".in_{dest.name}")
    try:
        with open(in_tmp, "wb") as out:
            async for chunk in body_stream:
                out.write(chunk)
    except Exception:
        Path(in_tmp).unlink(missing_ok=True)
        raise
    upload_id = uuid.uuid4().hex
    _COMPRESS_TASKS[upload_id] = asyncio.ensure_future(_compress_video(in_tmp, dest))
    return upload_id


def get_video_upload_status(upload_id: str) -> dict:
    """status는 "compressing"/"done"(path 포함)/"failed"(error 포함) 중
    하나. done/failed로 한 번 읽히면 더 폴링할 필요가 없으니 그 즉시
    레지스트리에서 지운다 — 안 지우면 재업로드를 계속하는 동안 사전에
    끝난 태스크들이 메모리에 무한정 쌓인다."""
    task = _COMPRESS_TASKS.get(upload_id)
    if task is None:
        raise KeyError(upload_id)
    if not task.done():
        return {"status": "compressing"}
    _COMPRESS_TASKS.pop(upload_id, None)
    exc = task.exception()
    if exc is not None:
        return {"status": "failed", "error": str(exc)}
    return {"status": "done", "path": task.result()}


def cleanup_orphaned_upload_temp_files() -> None:
    """서버 시작 시점에 media/video에 남아있는 .in_*/.out_* 파일은 전부
    주인 없는 임시 파일이다(그 시점엔 진행 중인 업로드가 있을 수 없다).
    실측: ffmpeg 압축 도중 서버 프로세스가 죽으면(재시작, OOM 등)
    try/except/finally가 아예 실행될 기회가 없어 정리가 안 된 채로 남는다."""
    video_dir = MEDIA_ROOT / "video"
    if not video_dir.exists():
        return
    for pattern in (".in_*", ".out_*"):
        for path in video_dir.glob(pattern):
            path.unlink(missing_ok=True)
