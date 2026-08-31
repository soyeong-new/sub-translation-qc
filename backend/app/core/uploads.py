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


_FASTSTART_PROBE_BYTES = 4 * 1024 * 1024  # moov/mdat 순서를 보기에 넉넉한 크기
_FFMPEG_VIDEO_ARGS = [
    "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy",
]


def _probably_faststart(header: bytes) -> bool:
    """header 앞부분의 MP4 박스(원자) 구조를 훑어 moov가 mdat보다 먼저
    나오는지 본다 — 정식 스펙 파서는 아니고 "파이프로 스트리밍 처리해도
    ffmpeg이 읽을 수 있는가"를 가늠하는 용도. 애매하면(박스를 못 읽거나
    둘 다 못 찾으면) False로 안전하게 판단해 임시 파일 폴백 경로로
    보낸다."""
    pos = 0
    n = len(header)
    while pos + 8 <= n:
        size = int.from_bytes(header[pos:pos + 4], "big")
        box_type = header[pos + 4:pos + 8]
        if box_type == b"moov":
            return True
        if box_type == b"mdat":
            return False
        if size == 1:  # 64비트 확장 크기 — largesize가 헤더 바로 뒤 8바이트
            if pos + 16 > n:
                break
            size = int.from_bytes(header[pos + 8:pos + 16], "big")
        if size < 8:
            break
        pos += size
    return False


async def _prepend(first: bytes, rest: AsyncIterator[bytes] | None) -> AsyncIterator[bytes]:
    if first:
        yield first
    if rest is not None:
        async for chunk in rest:
            yield chunk


async def _run_ffmpeg(args: list) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"영상 변환 실패: {stderr.decode(errors='replace')[-500:]}")


async def _pipe_into_ffmpeg(out_path: Path, body_stream: AsyncIterator[bytes]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", "pipe:0", *_FFMPEG_VIDEO_ARGS, str(out_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
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
            raise RuntimeError(f"영상 변환 실패: {stderr.decode(errors='replace')[-500:]}")
    except Exception:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise


async def save_video_upload_streamed(filename: str, body_stream: AsyncIterator[bytes]) -> str:
    """받은 영상을 디스크에 원본 그대로 복사하지 않고, 받는 즉시 ffmpeg으로
    압축하며 저장한다(design 2026-08-31, 갱신: 완전 자동화 — 실측: 9GB
    영상 하나에 순간적으로 18GB가 필요해 디스크가 꽉 차던 장애).

    업로드 시작 시 앞부분(_FASTSTART_PROBE_BYTES)만 먼저 살펴 moov(메타
    데이터) 원자가 파일 앞쪽에 있는지(faststart) 자동으로 판단한다:
    - faststart면 파이프(seek 불가능한 입력)로도 ffmpeg이 바로 읽을 수
      있어, 앞부분+나머지를 그대로 ffmpeg stdin에 흘려보낸다 — 순간
      필요 용량이 압축 결과물 수준으로 작다.
    - 아니면(moov가 뒤쪽) 파이프로는 ffmpeg이 스트림 정보를 못 찾아
      실패하므로(실측 확인), 자동으로 임시 파일에 받아 적은 뒤(seek
      가능해지므로) 그 파일을 ffmpeg 입력으로 압축한다 — 이 경우만
      원본 크기만큼의 여유 공간이 그때만 필요하다(moov 위치를 옮기려면
      파일을 한 번은 끝까지 읽어야 하는 물리적 한계라 피할 수 없다).
    사용자는 업로드 전에 아무것도 미리 할 필요가 없다 — 둘 중 어느
    쪽이든 서버가 알아서 판단해서 처리한다.

    ffmpeg 출력은 항상 임시 이름으로 먼저 쓰고 성공했을 때만 최종 경로로
    원자적으로 교체한다(os.replace) — 프로세스가 예외 없이 강제 종료되면
    (서버 재시작 등) except 블록이 못 도는데, 최종 경로에 바로 썼다면
    깨진 파일이 그대로 남는다. 임시 이름에만 썼다가 성공 후 바꿔치기하면
    그 경우에도 최종 경로엔 항상 "없거나, 완전한 파일"만 존재한다."""
    dest = build_upload_destination("video", filename, VIDEO_EXTENSIONS)
    out_tmp = dest.with_name(f".out_{dest.name}")

    stream_iter = body_stream.__aiter__()
    header = b""
    exhausted = False
    while len(header) < _FASTSTART_PROBE_BYTES:
        try:
            header += await stream_iter.__anext__()
        except StopAsyncIteration:
            exhausted = True
            break
    combined = _prepend(header, None if exhausted else stream_iter)

    if _probably_faststart(header):
        try:
            await _pipe_into_ffmpeg(out_tmp, combined)
            Path(out_tmp).replace(dest)
        except Exception:
            Path(out_tmp).unlink(missing_ok=True)
            raise
        return str(dest)

    # moov가 뒤쪽 — 임시 파일에 받아 적어야 ffmpeg이 seek해서 읽을 수 있다.
    in_tmp = dest.with_name(f".in_{dest.name}")
    try:
        with open(in_tmp, "wb") as out:
            async for chunk in combined:
                out.write(chunk)
        await _run_ffmpeg(["ffmpeg", "-y", "-i", str(in_tmp), *_FFMPEG_VIDEO_ARGS, str(out_tmp)])
        Path(out_tmp).replace(dest)
    except Exception:
        Path(out_tmp).unlink(missing_ok=True)
        raise
    finally:
        Path(in_tmp).unlink(missing_ok=True)
    return str(dest)
