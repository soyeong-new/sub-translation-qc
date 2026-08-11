"""작품(Title) 등록, 에피소드 등록, 목록 조회/삭제 엔드포인트."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, delete
from app.db import async_session
from app.models import (
    Title, Episode, TargetVersion, Segment, FindingRow, SttCorrection, ExportRow,
)
from app.core.validation import validate_english_srt_path, validate_korean_srt_path
from app.core.ingest import delete_original_video
from app.language_profiles.loader import list_profiles

router = APIRouter()


class TitleIn(BaseModel):
    name: str
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str
    english_srt_path: str | None = None
    korean_srt_path: str | None = None


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
    if payload.korean_srt_path is not None:
        validate_korean_srt_path(payload.korean_srt_path)
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        episode = Episode(title_id=title_id, episode_no=payload.episode_no,
                          video_path=payload.video_path,
                          english_srt_path=payload.english_srt_path,
                          korean_srt_path=payload.korean_srt_path)
        session.add(episode)
        await session.commit()
        return {"id": episode.id, "title_id": title_id}


@router.get("/titles")
async def list_titles():
    """제목별로 묶어서(타이틀명 아카이브) 각 title의 episode/target_version을
    같이 내려준다 — 프론트가 "다시 열기/재분석/삭제"를 목록에서 바로
    할 수 있게, 목록 하나로 필요한 정보를 다 준다(N+1 호출 방지). 최근
    등록 순으로 정렬한다."""
    async with async_session() as session:
        titles = (await session.execute(
            select(Title).order_by(Title.created_at.desc())
        )).scalars().all()
        episodes = (await session.execute(select(Episode))).scalars().all()
        target_versions = (await session.execute(select(TargetVersion))).scalars().all()

        tvs_by_episode: dict = {}
        for tv in target_versions:
            tvs_by_episode.setdefault(tv.episode_id, []).append({
                "id": tv.id, "target_language": tv.target_language, "variant": tv.variant,
                "status": tv.status, "error_message": tv.error_message,
            })
        episodes_by_title: dict = {}
        for ep in episodes:
            episodes_by_title.setdefault(ep.title_id, []).append({
                "id": ep.id, "episode_no": ep.episode_no,
                "target_versions": tvs_by_episode.get(ep.id, []),
            })

        return [
            {"id": t.id, "name": t.name, "type": t.type,
             "episodes": episodes_by_title.get(t.id, [])}
            for t in titles
        ]


@router.delete("/titles/{title_id}")
async def delete_title(title_id: str):
    """title과 그 아래 episode/target_version/segment/finding 등을 전부
    지운다. 원본 영상(성공 후엔 이미 지워져 있음)·프록시 영상 파일도 같이
    지운다 — 안 지우면 디스크만 계속 쌓인다."""
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")

        episodes = (await session.execute(
            select(Episode).where(Episode.title_id == title_id)
        )).scalars().all()
        episode_ids = [ep.id for ep in episodes]
        target_version_ids: list = []
        files_to_delete: list = []
        for ep in episodes:
            if ep.video_path:
                files_to_delete.append(ep.video_path)
            if ep.video_proxy_path:
                files_to_delete.append(ep.video_proxy_path)

        if episode_ids:
            tvs = (await session.execute(
                select(TargetVersion).where(TargetVersion.episode_id.in_(episode_ids))
            )).scalars().all()
            target_version_ids = [tv.id for tv in tvs]
            files_to_delete += [tv.video_proxy_path for tv in tvs if tv.video_proxy_path]

        if target_version_ids:
            await session.execute(delete(FindingRow).where(
                FindingRow.target_version_id.in_(target_version_ids)))
            await session.execute(delete(SttCorrection).where(
                SttCorrection.segment_id.in_(
                    select(Segment.id).where(Segment.target_version_id.in_(target_version_ids))
                )
            ))
            await session.execute(delete(Segment).where(
                Segment.target_version_id.in_(target_version_ids)))
            await session.execute(delete(ExportRow).where(
                ExportRow.target_version_id.in_(target_version_ids)))
            await session.execute(delete(TargetVersion).where(
                TargetVersion.id.in_(target_version_ids)))
        if episode_ids:
            await session.execute(delete(Episode).where(Episode.id.in_(episode_ids)))
        await session.delete(title)
        await session.commit()

    for path in files_to_delete:
        delete_original_video(path)  # missing_ok unlink — 재사용

    return {"deleted": True}
