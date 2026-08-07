"""영상/자막/인물관계도 이미지 업로드 엔드포인트."""

from fastapi import APIRouter, HTTPException, UploadFile, File
from app.core.uploads import (
    save_upload, UnsupportedFileType, VIDEO_EXTENSIONS, SRT_EXTENSIONS, IMAGE_EXTENSIONS,
)

router = APIRouter()


@router.post("/uploads/video")
async def upload_video(file: UploadFile = File(...)):
    try:
        path = await save_upload("video", file.filename, file.read, VIDEO_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@router.post("/uploads/srt")
async def upload_srt(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt", file.filename, file.read, SRT_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@router.post("/uploads/srt-en")
async def upload_srt_en(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt_en", file.filename, file.read, SRT_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@router.post("/uploads/chart-image")
async def upload_chart_image(file: UploadFile = File(...)):
    try:
        path = await save_upload("chart_image", file.filename, file.read, IMAGE_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}
