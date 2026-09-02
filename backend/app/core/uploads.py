"""업로드된 파일을 경로 조작 없이 로컬 디스크에 스트리밍 저장하는 모듈."""

import asyncio
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set

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


_UPLOAD_SESSIONS: dict[str, dict] = {}


async def save_video_chunk(
    upload_id: str, chunk_index: int, total_chunks: int, filename: str, data: bytes,
) -> Optional[str]:
    """청크 하나를 원본 임시 파일에 이어 쓴다. 마지막 청크(total_chunks - 1)
    까지 다 받으면 ffmpeg으로 압축해 최종 경로를 반환하고, 아니면 None을
    반환한다(호출자가 "다음 청크 보내라"고 응답).

    design 2026-09-02: 업로드 하나를 통짜 HTTP 요청 하나로 보내면, 연결이
    한 번만 끊겨도(브라우저 탭이 백그라운드로 밀리는 것만으로도 발생)
    수백MB~수GB를 처음부터 다시 보내야 했다. 파일을 작은 청크로 나눠
    순서대로 보내면, 청크 하나가 실패해도 그 청크만 재전송하면 된다.
    이미 받은 청크 번호가 다시 오면(응답을 못 받은 클라이언트의 재시도)
    조용히 무시해 중복으로 이어 쓰지 않는다.

    ponytail: 세션 상태는 프로세스 메모리 dict — 단일 uvicorn 워커라
    충돌 없다. 서버가 재시작되면 진행 중이던 세션은 유실된다(그 경우
    클라이언트가 새 upload_id로 처음부터 다시 시작해야 한다) — 순간적인
    네트워크 끊김을 감당하려는 목적이라 이 정도 한계는 감수할 만하다."""
    session = _UPLOAD_SESSIONS.get(upload_id)
    if session is None:
        if chunk_index != 0:
            raise RuntimeError("업로드 세션을 찾을 수 없습니다 — 처음부터 다시 시도해주세요.")
        dest = build_upload_destination("video", filename, VIDEO_EXTENSIONS)
        session = {"dest": dest, "in_tmp": dest.with_name(f".in_{dest.name}"), "next_index": 0}
        _UPLOAD_SESSIONS[upload_id] = session

    if chunk_index < session["next_index"]:
        # 이미 받은 청크의 재전송 — 무시하되, 그게 마지막 청크였다면(이미
        # 압축까지 끝났다면) 그때 응답을 못 받은 클라이언트를 위해 최종
        # 경로를 다시 돌려준다(그래야 클라이언트가 끝난 걸 알 수 있다).
        return session.get("done_path")
    if chunk_index != session["next_index"]:
        _UPLOAD_SESSIONS.pop(upload_id, None)
        Path(session["in_tmp"]).unlink(missing_ok=True)
        raise RuntimeError(f"청크 순서가 어긋났습니다(기대 {session['next_index']}, 받음 {chunk_index})")

    try:
        with open(session["in_tmp"], "ab") as out:
            out.write(data)
    except Exception:
        _UPLOAD_SESSIONS.pop(upload_id, None)
        Path(session["in_tmp"]).unlink(missing_ok=True)
        raise
    session["next_index"] += 1

    if chunk_index != total_chunks - 1:
        return None

    dest = session["dest"]
    out_tmp = dest.with_name(f".out_{dest.name}")
    try:
        await _run_ffmpeg(["ffmpeg", "-y", "-i", str(session["in_tmp"]), *_FFMPEG_VIDEO_ARGS, str(out_tmp)])
        Path(out_tmp).replace(dest)
    except Exception:
        _UPLOAD_SESSIONS.pop(upload_id, None)
        Path(out_tmp).unlink(missing_ok=True)
        raise
    finally:
        Path(session["in_tmp"]).unlink(missing_ok=True)
    # ponytail: 성공한 세션은 done_path만 남기고 dict에 그대로 둔다(늦게
    # 오는 마지막 청크 재시도에 응답하기 위해) — 프로세스 수명 내내 조금씩
    # 쌓이지만 항목이 가벼워(Path 두어 개) 이 앱 사용량에선 무해하다.
    # 문제가 되면 그때 TTL 청소를 추가한다.
    session["done_path"] = str(dest)
    return str(dest)


def abandon_video_chunk_upload(upload_id: str) -> None:
    """청크 재전송까지 다 실패해 프론트가 업로드를 포기했을 때 호출한다.
    이 신호가 없으면 서버는 업로드가 실패했다는 걸 알 방법이 없어, 이어
    붙이던 임시 원본 파일(.in_...)이 디스크에 영영 남는다(실측)."""
    session = _UPLOAD_SESSIONS.pop(upload_id, None)
    if session is not None:
        Path(session["in_tmp"]).unlink(missing_ok=True)
