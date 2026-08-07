"""FastAPI 앱 생성, 정적 파일 마운트, 라우터 등록. 엔드포인트 구현은 app/routers/*."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.core.uploads import MEDIA_ROOT
from app.routers import titles, characters, relationships, analysis, findings, export, uploads


app = FastAPI(title="Sub Translation QC ES")

(MEDIA_ROOT / "video_proxy").mkdir(parents=True, exist_ok=True)
(MEDIA_ROOT / "chart_image").mkdir(parents=True, exist_ok=True)
# 프록시/이미지 디렉터리만 마운트한다 — media/ 전체를 마운트하면 원본 업로드 영상
# (media/video/)과 원본 SRT(media/srt/)까지 인증 없이 그대로 서빙돼버린다.
# 검수 화면이 실제로 필요로 하는 건 저화질 프록시와 인물관계도 이미지뿐이다.
app.mount("/media/video_proxy", StaticFiles(directory=str(MEDIA_ROOT / "video_proxy")),
          name="media_video_proxy")
app.mount("/media/chart_image", StaticFiles(directory=str(MEDIA_ROOT / "chart_image")),
          name="media_chart_image")


@app.on_event("startup")
async def _init_background_task_registry():
    # asyncio는 참조가 없는 태스크를 도중에 가비지컬렉션할 수 있다(공식 문서
    # 권고사항) — run-analysis가 만든 태스크를 여기 보관해 완료 전에 사라지지
    # 않게 한다.
    app.state.background_tasks = set()


app.include_router(titles.router)
app.include_router(characters.router)
app.include_router(relationships.router)
app.include_router(analysis.router)
app.include_router(findings.router)
app.include_router(export.router)
app.include_router(uploads.router)
