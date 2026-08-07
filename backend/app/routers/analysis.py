"""대상언어 버전(TargetVersion) 생성/조회 및 분석 실행 엔드포인트."""

import asyncio
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from app.db import async_session
from app.models import Episode, TargetVersion
from app.core.uploads import MEDIA_ROOT
from app.repositories import delete_target_version_results
from app.background import analyze_and_save

router = APIRouter()


class TargetVersionIn(BaseModel):
    target_language: str
    variant: str


class RunAnalysisIn(BaseModel):
    target_srt_path: str


@router.post("/episodes/{episode_id}/target-versions")
async def create_target_version(episode_id: str, payload: TargetVersionIn):
    async with async_session() as session:
        episode = await session.get(Episode, episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        tv = TargetVersion(episode_id=episode_id, target_language=payload.target_language,
                           variant=payload.variant, status="analyzing")
        session.add(tv)
        await session.commit()
        return {"id": tv.id, "status": tv.status}


@router.get("/target-versions/{target_version_id}")
async def get_target_version(target_version_id: str):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        video_proxy_url = (
            f"/media/video_proxy/{Path(tv.video_proxy_path).relative_to(MEDIA_ROOT / 'video_proxy')}"
            if tv.video_proxy_path else None
        )
        return {"id": tv.id, "status": tv.status, "error_message": tv.error_message,
                "video_proxy_url": video_proxy_url, "warnings": tv.warnings or [],
                "title_id": episode.title_id if episode else None}


@router.post("/target-versions/{target_version_id}/run-analysis")
async def run_analysis(target_version_id: str, payload: RunAnalysisIn, request: Request):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        # 재시도(이미 한 번 분석된 target_version에 다시 요청)일 수 있으므로,
        # 이전 실행의 Segment/Finding을 먼저 지운다 — 요청이 끝나기 전에
        # 동기적으로 처리해 폴링하는 클라이언트가 옛 결과를 잠깐이라도 보지
        # 않게 한다.
        await delete_target_version_results(session, target_version_id)
        tv.status = "analyzing"
        tv.error_message = None
        tv.warnings = None
        await session.commit()

    task = asyncio.create_task(analyze_and_save(target_version_id, payload.target_srt_path))
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)
    return {"status": "analyzing"}
