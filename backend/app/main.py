"""FastAPI 앱 생성, 정적 파일 마운트, 라우터 등록. 엔드포인트 구현은 app/routers/*."""

import shutil
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from app.core.uploads import MEDIA_ROOT
from app.db import async_session
from app.models import TargetVersion
from app.routers import titles, analysis, findings, export, uploads


app = FastAPI(title="Sub Translation QC ES")

# 업로드 처리 중 만들어지는 프록시/오디오 추출본 용량 여유분(design
# 2026-08-31 — 실측: 9GB 영상을 여유 공간 7GB 서버에 올렸더니 우리 코드에
# 도달하기도 전에 Starlette가 본문을 파싱하는 중 디스크가 꽉 차 "There was
# an error parsing the body"라는 알아보기 힘든 영어 에러로 실패했다). srt류
# 업로드는 원본 크기만큼 그대로 저장하므로 이 값을 계속 쓴다.
UPLOAD_SAFETY_MARGIN_BYTES = 1024 ** 3

# /uploads/video는 이제 원본을 그대로 저장하지 않고 받으면서 바로 ffmpeg으로
# 압축한다(save_video_upload_streamed, design 2026-08-31) — 그래서 필요
# 용량이 원본 크기와 무관해졌다. 압축 결과물 + 이후 480p 프록시/오디오
# 추출 여유분만 고정으로 확인하면 된다(원본 Content-Length 기준 계산은
# 더 이상 의미가 없다 — 오히려 큰 원본을 불필요하게 거부하게 된다).
VIDEO_UPLOAD_MIN_FREE_BYTES = 2 * 1024 ** 3


def _insufficient_storage_response(needed: int, free: int) -> JSONResponse:
    return JSONResponse(
        status_code=507,
        content={"detail": (
            "디스크 공간이 부족해 업로드할 수 없습니다 "
            f"(필요: {needed / 1024**3:.1f}GB, 여유: {free / 1024**3:.1f}GB)."
        )},
    )


@app.middleware("http")
async def _reject_uploads_when_disk_low(request: Request, call_next):
    """/uploads/*는 본문을 라우터가 아니라 FastAPI/Starlette가 먼저
    파싱하면서 디스크에 임시로 받아 적는다 — 그래서 엔드포인트 함수 안에서
    체크하면 이미 늦다. 여유 공간을 미들웨어에서 미리 보고, 부족하면
    본문을 읽기 시작하기도 전에 명확한 에러로 막는다."""
    if request.method == "POST" and request.url.path.startswith("/uploads/"):
        free = shutil.disk_usage(MEDIA_ROOT).free
        if request.url.path == "/uploads/video":
            if free < VIDEO_UPLOAD_MIN_FREE_BYTES:
                return _insufficient_storage_response(VIDEO_UPLOAD_MIN_FREE_BYTES, free)
        else:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                needed = int(content_length) + UPLOAD_SAFETY_MARGIN_BYTES
                if needed > free:
                    return _insufficient_storage_response(needed, free)
    return await call_next(request)

(MEDIA_ROOT / "video_proxy").mkdir(parents=True, exist_ok=True)
# 프록시 디렉터리만 마운트한다 — media/ 전체를 마운트하면 원본 업로드 영상
# (media/video/)과 원본 SRT(media/srt/)까지 인증 없이 그대로 서빙돼버린다.
# 검수 화면이 실제로 필요로 하는 건 저화질 프록시뿐이다.
app.mount("/media/video_proxy", StaticFiles(directory=str(MEDIA_ROOT / "video_proxy")),
          name="media_video_proxy")


async def _fail_stuck_in_progress_target_versions() -> None:
    """"analyzing"/"verifying" 상태로 멈춰있는 target_version을 "failed"로
    되돌린다 — 이 상태를 만든 백그라운드 태스크(analyze_and_save/
    _run_phase2_and_save)는 asyncio.create_task로 떠 있을 뿐이라, 서버
    프로세스가 재시작되면(개발 중 --reload, 배포, 장애 등) 진행 중이던
    작업이 그대로 죽고 DB status만 영원히 "진행 중"에 남는다. 프론트는
    이 상태를 "review"/"awaiting_confirmation"/"failed"가 될 때까지
    폴링하므로(api.js의 pollTargetVersionStatus), 아무도 안 끝내주면
    검수자 눈에는 "열어도 계속 검증만 하고 안 끝난다"로 보인다 — "failed"로
    돌려놓아야 폴링이 끝나고 재분석(새로고침)을 안내할 수 있다."""
    async with async_session() as session:
        stuck = (await session.execute(
            select(TargetVersion).where(TargetVersion.status.in_(["analyzing", "verifying"]))
        )).scalars().all()
        for tv in stuck:
            tv.status = "failed"
            tv.error_message = (
                "서버 재시작으로 진행 중이던 작업이 중단됐습니다 — 새로고침으로 다시 분석해주세요."
            )
        if stuck:
            await session.commit()


@app.on_event("startup")
async def _init_background_task_registry():
    # asyncio는 참조가 없는 태스크를 도중에 가비지컬렉션할 수 있다(공식 문서
    # 권고사항) — run-analysis가 만든 태스크를 여기 보관해 완료 전에 사라지지
    # 않게 한다.
    app.state.background_tasks = set()
    await _fail_stuck_in_progress_target_versions()


app.include_router(titles.router)
app.include_router(analysis.router)
app.include_router(findings.router)
app.include_router(export.router)
app.include_router(uploads.router)
