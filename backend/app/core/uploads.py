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


async def save_video_upload_streamed(filename: str, body_stream: AsyncIterator[bytes]) -> str:
    """받은 영상을 디스크에 원본 그대로 복사하지 않고, 받는 즉시 ffmpeg으로
    압축하며 저장한다(design 2026-08-31 — 실측: 9GB 영상 하나에 순간적으로
    18GB가 필요해 디스크가 꽉 차던 장애). FastAPI의 자동 UploadFile 파싱은
    원본 전체를 먼저 통째로 임시 저장하는데, 거기에 우리가 또 원본 크기만큼
    복사본을 만들면 최대 원본의 2배 용량이 필요하다. 라우터가
    request.form() 대신 request.stream()으로 받은 원본 바이트를 그대로
    ffmpeg stdin에 흘려보내면, 디스크엔 압축된 작은 결과물만 남는다.

    전제 조건: 입력 영상이 faststart(moov 원자가 파일 앞쪽)여야 한다 —
    아니면 ffmpeg이 파이프(seek 불가능한 입력)에서 스트림 정보를 못 찾아
    실패한다(실측 확인: 수백KB급 작은 파일은 우연히 성공하지만, 수백MB급
    이상은 거의 항상 "Invalid data found when processing input"로 실패).
    업로드 전 로컬에서 `ffmpeg -c copy -movflags +faststart`로 변환해야
    한다 — 재인코딩이 아니라 메타데이터 위치만 옮기는 거라 순식간에 끝난다."""
    dest = build_upload_destination("video", filename, VIDEO_EXTENSIONS)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", "pipe:0",
        "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        str(dest),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async for chunk in body_stream:
            proc.stdin.write(chunk)
            await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()
        stderr = await proc.stderr.read()
        returncode = await proc.wait()
        if returncode != 0:
            raise RuntimeError(
                "영상 변환 실패 — 업로드 전 영상을 faststart로 변환했는지 "
                "확인하세요(ffmpeg -c copy -movflags +faststart). "
                f"ffmpeg 오류: {stderr.decode(errors='replace')[-500:]}"
            )
    except Exception:
        Path(dest).unlink(missing_ok=True)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return str(dest)
