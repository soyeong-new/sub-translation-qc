"""FastAPI 앱 생성, 정적 파일 마운트, 라우터 등록. 엔드포인트 구현은 app/routers/*."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from app.core.uploads import MEDIA_ROOT
from app.db import async_session
from app.models import TargetVersion
from app.routers import titles, analysis, findings, export, uploads


app = FastAPI(title="Sub Translation QC ES")

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
