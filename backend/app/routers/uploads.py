"""영상/자막 업로드 엔드포인트."""

from urllib.parse import unquote
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from app.core.uploads import (
    save_upload, save_video_upload_streamed, UnsupportedFileType, VIDEO_EXTENSIONS, SRT_EXTENSIONS,
)

router = APIRouter()


@router.post("/uploads/video")
async def upload_video(request: Request):
    """multipart가 아니라 요청 본문 그대로(raw body) 받는다 — FastAPI의
    UploadFile은 원본 전체를 먼저 통째로 임시 저장해버려서, 그 위에 우리가
    또 복사본을 만들면 순간적으로 원본의 2배 용량이 필요하다(design
    2026-08-31, 실측 장애). request.stream()으로 받은 바이트를 그대로
    ffmpeg에 흘려보내 압축된 결과만 저장한다. 파일명은 멀티파트 본문 안이
    아니라 헤더로 받는다(프론트는 encodeURIComponent로 인코딩해서 보낸다)."""
    filename = request.headers.get("x-filename")
    if not filename:
        raise HTTPException(400, "X-Filename 헤더가 필요합니다")
    try:
        path = await save_video_upload_streamed(unquote(filename), request.stream())
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@router.post("/uploads/srt")
async def upload_srt(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt", file.filename, file.read, SRT_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@router.post("/uploads/srt-ko")
async def upload_srt_ko(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt_ko", file.filename, file.read, SRT_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}
