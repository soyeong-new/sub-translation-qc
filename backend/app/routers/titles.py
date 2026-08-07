"""작품(Title) 등록, 에피소드 등록 엔드포인트."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import Title, Episode
from app.core.validation import validate_english_srt_path
from app.language_profiles.loader import list_profiles

router = APIRouter()


class TitleIn(BaseModel):
    name: str
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str
    english_srt_path: str | None = None


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


@router.get("/titles")
async def list_titles():
    async with async_session() as session:
        rows = (await session.execute(select(Title))).scalars().all()
        return [{"id": t.id, "name": t.name, "type": t.type} for t in rows]


@router.get("/titles/{title_id}")
async def get_title(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        return {"id": title.id, "name": title.name, "type": title.type}
