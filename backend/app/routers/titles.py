"""작품(Title) 등록, 에피소드 등록, 목록 조회/삭제 엔드포인트."""

import shutil
from datetime import datetime, timezone
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import Title, Episode, TargetVersion, FindingRow
from app.core.validation import validate_korean_srt_path
from app.core.ingest import delete_original_video
from app.core.uploads import MEDIA_ROOT
from app.language_profiles.loader import list_profiles

router = APIRouter()


class TitleIn(BaseModel):
    name: str
    type: str


class TitleUpdateIn(BaseModel):
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str
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


@router.patch("/titles/{title_id}")
async def update_title(title_id: str, payload: TitleUpdateIn):
    """등록할 때 유형(영화/드라마)을 잘못 골랐을 때 고치는 용도 — 예를 들어
    영화로 등록해서 "회차 추가" 버튼이 안 보이던 걸 드라마로 바꾸면 바로
    보이게 된다."""
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None or title.deleted_at is not None:
            raise HTTPException(404, "title not found")
        title.type = payload.type
        await session.commit()
        return {"id": title.id, "name": title.name, "type": title.type}


@router.post("/titles/{title_id}/episodes")
async def create_episode(title_id: str, payload: EpisodeIn):
    if payload.korean_srt_path is not None:
        validate_korean_srt_path(payload.korean_srt_path)
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        episode = Episode(title_id=title_id, episode_no=payload.episode_no,
                          video_path=payload.video_path,
                          korean_srt_path=payload.korean_srt_path)
        session.add(episode)
        await session.commit()
        return {"id": episode.id, "title_id": title_id}


@router.get("/storage")
async def get_storage_usage():
    """MEDIA_ROOT가 놓인 디스크(EC2라면 EBS 볼륨)의 전체/사용 용량을 바이트로 반환한다."""
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(MEDIA_ROOT)
    return {"used": usage.used, "total": usage.total}


@router.get("/titles")
async def list_titles():
    """제목별로 묶어서(타이틀명 아카이브) 각 title의 episode/target_version을
    같이 내려준다 — 프론트가 "다시 열기/재분석/삭제"를 목록에서 바로
    할 수 있게, 목록 하나로 필요한 정보를 다 준다(N+1 호출 방지). 최근
    등록 순으로 정렬한다."""
    async with async_session() as session:
        titles = (await session.execute(
            select(Title).where(Title.deleted_at.is_(None)).order_by(Title.created_at.desc())
        )).scalars().all()
        episodes = (await session.execute(
            select(Episode).order_by(Episode.episode_no)
        )).scalars().all()
        target_versions = (await session.execute(select(TargetVersion))).scalars().all()
        reviewer_rows = (await session.execute(
            select(FindingRow.target_version_id, FindingRow.reviewer_name)
            .where(FindingRow.reviewer_name != "")
        )).all()
        reviewers_by_tv: dict = {}
        for tv_id, name in reviewer_rows:
            reviewers_by_tv.setdefault(tv_id, set()).add(name)

        display_names = {
            (p["language"], p["variant"]): p["display_name"] for p in list_profiles()
        }
        tvs_by_episode: dict = {}
        for tv in target_versions:
            tvs_by_episode.setdefault(tv.episode_id, []).append({
                "id": tv.id, "target_language": tv.target_language, "variant": tv.variant,
                "display_name": display_names.get(
                    (tv.target_language, tv.variant), f"{tv.target_language} ({tv.variant})"
                ),
                "status": tv.status, "error_message": tv.error_message,
                "reviewers": sorted(reviewers_by_tv.get(tv.id, [])),
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
    """title을 소프트 삭제한다(deleted_at만 세팅) — episode/target_version/
    segment/finding/SttCorrection 등 교정 이력은 DB에 그대로 남긴다(나중에
    QC 이력 재활용 용도로 쓰기 위해). 목록 조회(list_titles)에서만 안 보이게
    걸러진다. 용량만 차지하는 원본/프록시 영상 파일은 디스크에서 실제로
    지운다."""
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None or title.deleted_at is not None:
            raise HTTPException(404, "title not found")

        episodes = (await session.execute(
            select(Episode).where(Episode.title_id == title_id)
        )).scalars().all()
        episode_ids = [ep.id for ep in episodes]
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
            files_to_delete += [tv.video_proxy_path for tv in tvs if tv.video_proxy_path]

        title.deleted_at = datetime.now(timezone.utc)
        await session.commit()

    for path in files_to_delete:
        delete_original_video(path)  # missing_ok unlink — 재사용

    return {"deleted": True}
