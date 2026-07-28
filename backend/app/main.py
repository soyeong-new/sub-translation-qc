from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.db import async_session
from app.models import Title, Episode, TargetVersion
from app.core.pipeline import run_pipeline
from app.core.ingest import extract_audio  # noqa: F401 (테스트에서 patch 대상)
from app.providers.base import get_provider
from app.repositories import save_pipeline_result, get_findings as repo_get_findings

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


class RunAnalysisIn(BaseModel):
    target_srt_path: str


@app.post("/target-versions/{target_version_id}/run-analysis")
async def run_analysis(target_version_id: str, payload: RunAnalysisIn):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)

    provider = get_provider()
    result = await run_pipeline(
        korean_audio_path=episode.video_path,
        target_srt_path=payload.target_srt_path,
        language=tv.target_language, variant=tv.variant,
        target_version_id=target_version_id, provider=provider,
    )

    async with async_session() as session:
        await save_pipeline_result(session, target_version_id, result)
        tv = await session.get(TargetVersion, target_version_id)
        tv.status = "review"
        await session.commit()
    return {"status": "review", "finding_count": len(result["findings"])}


@app.get("/target-versions/{target_version_id}/findings")
async def list_findings(target_version_id: str):
    async with async_session() as session:
        rows = await repo_get_findings(session, target_version_id)
        return [
            {"id": r.id, "segment_id": r.segment_id, "category": r.category,
             "description": r.description, "original_text": r.original_text,
             "suggested_text": r.suggested_text, "status": r.status}
            for r in rows
        ]
