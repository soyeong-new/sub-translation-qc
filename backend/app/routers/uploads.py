"""영상/자막 업로드 엔드포인트."""

from urllib.parse import unquote
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from app.core.uploads import (
    save_upload, start_video_upload, get_video_upload_status, UnsupportedFileType, SRT_EXTENSIONS,
)

router = APIRouter()


@router.post("/uploads/video")
async def upload_video(request: Request):
    """multipart가 아니라 요청 본문 그대로(raw body) 받는다 — FastAPI의
    UploadFile은 원본 전체를 먼저 통째로 임시 저장해버려서, 그 위에 우리가
    또 복사본을 만들면 순간적으로 원본의 2배 용량이 필요하다. 파일명은
    멀티파트 본문 안이 아니라 헤더로 받는다(프론트는 encodeURIComponent로
    인코딩해서 보낸다). 원본 저장이 끝나면 압축을 기다리지 않고 곧장
    upload_id를 돌려준다 — 압축 완료는 아래 status 엔드포인트로 폴링한다."""
    filename = request.headers.get("x-filename")
    if not filename:
        raise HTTPException(400, "X-Filename 헤더가 필요합니다")
    try:
        upload_id = await start_video_upload(unquote(filename), request.stream())
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"upload_id": upload_id}


@router.get("/uploads/video/{upload_id}/status")
async def video_upload_status(upload_id: str):
    try:
        return get_video_upload_status(upload_id)
    except KeyError:
        raise HTTPException(404, "존재하지 않는 업로드입니다")


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
