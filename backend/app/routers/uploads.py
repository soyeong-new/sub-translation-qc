"""영상/자막 업로드 엔드포인트."""

from urllib.parse import unquote
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from app.core.uploads import (
    save_upload, save_video_chunk, UnsupportedFileType, SRT_EXTENSIONS,
)

router = APIRouter()


@router.post("/uploads/video/chunk")
async def upload_video_chunk(request: Request):
    """영상을 청크(조각) 단위로 순서대로 받는다(design 2026-09-02) —
    한 청크가 네트워크 문제로 실패해도 그 청크만 재전송하면 되고, 파일
    전체를 처음부터 다시 보낼 필요가 없다. 마지막 청크를 받으면 그 자리에서
    압축까지 마치고 최종 경로를 반환하며, 그 전까지는 다음 청크를
    요청하는 응답만 돌려준다. 파일명/청크 정보는 본문이 아니라 헤더로
    받는다(프론트는 encodeURIComponent로 인코딩해서 보낸다)."""
    filename = request.headers.get("x-filename")
    upload_id = request.headers.get("x-upload-id")
    chunk_index = request.headers.get("x-chunk-index")
    total_chunks = request.headers.get("x-total-chunks")
    if not filename or not upload_id or chunk_index is None or total_chunks is None:
        raise HTTPException(
            400, "X-Filename/X-Upload-Id/X-Chunk-Index/X-Total-Chunks 헤더가 필요합니다",
        )
    data = await request.body()
    try:
        path = await save_video_chunk(
            upload_id, int(chunk_index), int(total_chunks), unquote(filename), data,
        )
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    if path is None:
        return {"status": "chunk-received"}
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
