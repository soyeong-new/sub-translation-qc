"""대상언어 버전(TargetVersion) 생성/조회 및 분석 실행 엔드포인트."""

import asyncio
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from app.db import async_session
from app.models import Episode, TargetVersion, Segment
from app.core.uploads import MEDIA_ROOT
from app.repositories import delete_target_version_results, upsert_character_gender_facts
from app.background import analyze_and_save, _run_phase2_and_save
from app.providers.base import get_provider
from app.core.pipeline import gender_groups_all_resolved

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
        video_proxy_url = None
        if tv.video_proxy_path:
            try:
                video_proxy_url = (
                    f"/media/video_proxy/{Path(tv.video_proxy_path).relative_to(MEDIA_ROOT / 'video_proxy')}"
                )
            except ValueError:
                # 저장된 경로가 지금 MEDIA_ROOT 밖을 가리키면(다른 환경에서
                # 만들어진 stale 경로, 파일 유실 등) 영상 미리보기만 못 보여줄
                # 뿐 화면 전체가 500으로 죽으면 안 된다 — findings/segments는
                # 이 값과 무관하게 정상 조회돼야 한다.
                video_proxy_url = None
        return {"id": tv.id, "status": tv.status, "error_message": tv.error_message,
                "video_proxy_url": video_proxy_url, "warnings": tv.warnings or [],
                "video_offset_seconds": tv.video_offset_seconds or 0.0,
                "title_id": episode.title_id if episode else None}


async def _start_analysis(target_version_id: str, target_srt_path: str, request: Request) -> dict:
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
        tv.target_srt_path = target_srt_path
        await session.commit()

    task = asyncio.create_task(analyze_and_save(target_version_id, target_srt_path))
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)
    return {"status": "analyzing"}


@router.post("/target-versions/{target_version_id}/run-analysis")
async def run_analysis(target_version_id: str, payload: RunAnalysisIn, request: Request):
    return await _start_analysis(target_version_id, payload.target_srt_path, request)


@router.post("/target-versions/{target_version_id}/rerun")
async def rerun_analysis(target_version_id: str, request: Request):
    """"새로고침" 재분석 — 파일을 다시 업로드하지 않고, 최초 run-analysis 때
    저장해둔 target_srt_path로 처음부터 다시 돈다(STT 캐시는 episode 단위로
    남아있으면 재사용되지만, granularity가 안 맞으면 자동으로 새로 돈다)."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        if not tv.target_srt_path:
            raise HTTPException(400, "재분석할 SRT 경로가 없습니다 — run-analysis를 먼저 실행해야 합니다")
        target_srt_path = tv.target_srt_path
    return await _start_analysis(target_version_id, target_srt_path, request)


@router.post("/target-versions/{target_version_id}/confirm-registers")
async def confirm_registers(target_version_id: str, request: Request):
    """성별/격식 확인 페이지에서 사람이 답을 다 마친 뒤 호출한다 — 확인이
    끝나지 않은 줄이 남아 있으면 거부하고(추측으로 AI 검증을 시작하면 안
    되므로), 다 끝났으면 S2(AI 검증)를 새 백그라운드 태스크로 시작한다."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        if tv.status != "awaiting_confirmation":
            raise HTTPException(400, f"확인 대기 상태가 아닙니다 (현재 status={tv.status})")
        # 성별은 한 줄에 인물이 둘 이상이면(resolved_gender_groups_raw) 그룹별로
        # 다 답해야 확인된 것이다 — resolved_gender_raw만 보는 단순 SQL NULL
        # 체크로는 이 경우를 표현할 수 없어(그 줄은 resolved_gender_raw를 아예
        # 쓰지 않음) 후보를 가져와 파이썬에서 gender_groups_all_resolved로
        # 판단한다.
        candidates = (await session.execute(
            select(Segment).where(
                Segment.target_version_id == target_version_id,
                (Segment.gender_check_needed == True) | (Segment.formality_check_needed == True),  # noqa: E712
            )
        )).scalars().all()
        unresolved = any(
            (
                seg.gender_check_needed
                and not seg.resolved_gender_raw
                and not gender_groups_all_resolved(seg.resolved_gender_groups_raw)
            )
            or (seg.formality_check_needed and not seg.resolved_formality_raw)
            for seg in candidates
        )
        if unresolved:
            raise HTTPException(400, "아직 확인되지 않은 줄이 있습니다")

        # 이번에 확정된 성별 중 캐릭터 고유 이름이 있는 것만 title 단위로
        # 저장해, 다음 회차/언어판에서 재사용한다(design §시리즈/다국어
        # 간 캐릭터 성별 재사용). LLM의 1차 판정이 아니라 여기(확인 화면
        # 통과 시점)의 최종값만 저장한다.
        episode = await session.get(Episode, tv.episode_id)
        new_facts: dict = {}
        for seg in candidates:
            for group in (seg.resolved_gender_groups_raw or []):
                name = group.get("character_name")
                gender = group.get("gender")
                if name and gender in ("male", "female") and group.get("human_confirmed"):
                    new_facts[name] = gender
        if new_facts:
            await upsert_character_gender_facts(session, episode.title_id, new_facts)

        tv.status = "verifying"
        await session.commit()

    provider = get_provider()
    task = asyncio.create_task(_run_phase2_and_save(target_version_id, provider))
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)
    return {"status": "verifying"}
