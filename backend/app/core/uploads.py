"""업로드된 파일을 경로 조작 없이 로컬 디스크에 스트리밍 저장하는 모듈."""

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


async def save_video_upload(filename: str, body_stream: AsyncIterator[bytes]) -> str:
    """원본을 압축하지 않고 그대로 저장한다(design 2026-09-02, 실측: 업로드
    시점 720p 압축은 검수용 480p 프록시 생성(generate_video_proxy) 때 어차피
    다시 인코딩되는 이중 작업이었다 — 60분 영상 기준 압축에만 30분 넘게
    걸렸는데, 그 720p 결과물은 프록시 생성 직후 버려져 최종적으로 아무도
    쓰지 않았다. STT 오디오 추출/프록시 생성은 원본에서 바로 해도 무방하다
    (원본은 S1 종료 시 어차피 삭제됨, delete_original_video 참고) — 압축을
    없애면 이 대기 시간이 통째로 사라진다.

    body_stream은 request.stream() 같은 AsyncIterator[bytes]다(save_upload의
    read_chunk와 달리 크기 지정 read가 아니라 그냥 순회). multipart가 아니라
    요청 본문 그대로 받는 이유는 라우터 쪽 설명 참고 — FastAPI의 UploadFile은
    원본 전체를 먼저 통째로 임시 저장해버려서, 그 위에 우리가 또 복사본을
    만들면 순간적으로 원본의 2배 용량이 필요하다."""
    dest = build_upload_destination("video", filename, VIDEO_EXTENSIONS)
    try:
        with open(dest, "wb") as out:
            async for chunk in body_stream:
                out.write(chunk)
    except Exception:
        Path(dest).unlink(missing_ok=True)
        raise
    return str(dest)
