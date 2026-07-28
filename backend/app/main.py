from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.db import async_session
from app.models import Title, Episode, TargetVersion

app = FastAPI(title="Sub Translation QC ES")


class TitleIn(BaseModel):
    name: str
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str


class TargetVersionIn(BaseModel):
    target_language: str
    variant: str


@app.post("/titles")
async def create_title(payload: TitleIn):
    async with async_session() as session:
        title = Title(name=payload.name, type=payload.type)
        session.add(title)
        await session.commit()
        return {"id": title.id, "name": title.name, "type": title.type}


@app.post("/titles/{title_id}/episodes")
async def create_episode(title_id: str, payload: EpisodeIn):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        episode = Episode(title_id=title_id, episode_no=payload.episode_no,
                          video_path=payload.video_path)
        session.add(episode)
        await session.commit()
        return {"id": episode.id, "title_id": title_id}


@app.post("/episodes/{episode_id}/target-versions")
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
