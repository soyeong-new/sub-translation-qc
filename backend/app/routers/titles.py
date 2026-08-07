"""작품(Title) 등록, 에피소드 등록, 인물관계도 이미지 첨부 엔드포인트."""

import asyncio
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from app.db import async_session
from app.models import Title, Episode
from app.core.uploads import MEDIA_ROOT
from app.core.validation import validate_chart_image_path, validate_english_srt_path
from app.language_profiles.loader import list_profiles
from app.background import extract_chart_and_save

router = APIRouter()


class TitleIn(BaseModel):
    name: str
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str
    english_srt_path: str | None = None


class ChartImageIn(BaseModel):
    image_path: str


@router.get("/language-profiles")
async def get_language_profiles():
    return list_profiles()


@router.post("/titles")
async def create_title(payload: TitleIn):
    async with async_session() as session:
        title = Title(name=payload.name, type=payload.type)
        session.add(title)
        await session.commit()
        return {"id": title.id, "name": title.name, "type": title.type}


@router.post("/titles/{title_id}/episodes")
async def create_episode(title_id: str, payload: EpisodeIn):
    if payload.english_srt_path is not None:
        validate_english_srt_path(payload.english_srt_path)
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        episode = Episode(title_id=title_id, episode_no=payload.episode_no,
                          video_path=payload.video_path,
                          english_srt_path=payload.english_srt_path)
        session.add(episode)
        await session.commit()
        return {"id": episode.id, "title_id": title_id}


@router.post("/titles/{title_id}/chart-image")
async def attach_chart_image(title_id: str, payload: ChartImageIn, request: Request):
    validate_chart_image_path(payload.image_path)
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        title.chart_image_path = payload.image_path
        title.chart_extraction_status = "processing"
        title.chart_extraction_error = None
        await session.commit()

    task = asyncio.create_task(extract_chart_and_save(title_id, payload.image_path))
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)
    return {"status": "processing"}


@router.get("/titles")
async def list_titles():
    async with async_session() as session:
        rows = (await session.execute(select(Title))).scalars().all()
        return [
            {"id": t.id, "name": t.name, "type": t.type,
             "chart_extraction_status": t.chart_extraction_status}
            for t in rows
        ]


@router.get("/titles/{title_id}")
async def get_title(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        chart_image_url = None
        if title.chart_image_path:
            chart_path = Path(title.chart_image_path)
            chart_dir = MEDIA_ROOT / "chart_image"
            # is_relative_to/relative_to는 경로를 lexical하게만 비교하므로 ".."이
            # 섞인 chart_image_path(검증 없이 저장되는 클라이언트 입력)가 실제로는
            # chart_dir 밖을 가리켜도 접두사만 보고 통과시킬 수 있다 — resolve() 후
            # 비교해야 실제 위치 기준으로 안전하게 판단할 수 있다 (language_profiles/
            # loader.py의 load_profile과 동일한 패턴).
            try:
                resolved_path = chart_path.resolve()
                resolved_dir = chart_dir.resolve()
                if resolved_path.is_relative_to(resolved_dir):
                    chart_image_url = f"/media/chart_image/{resolved_path.relative_to(resolved_dir)}"
            except ValueError:
                # is_relative_to는 실제로 발생하지 않지만(둘 다 이미 resolve됨),
                # 방어적으로 다른 드라이브(Windows) 등 예외 상황도 None으로 처리한다.
                pass
        return {
            "id": title.id, "name": title.name, "type": title.type,
            "chart_extraction_status": title.chart_extraction_status,
            "chart_extraction_error": title.chart_extraction_error,
            "chart_image_url": chart_image_url,
        }


@router.post("/titles/{title_id}/chart/confirm")
async def confirm_chart(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        title.chart_extraction_status = "confirmed"
        await session.commit()
        return {"id": title.id, "chart_extraction_status": title.chart_extraction_status}
