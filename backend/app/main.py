"""작품 등록부터 분석 실행, 검수, export까지 담당하는 FastAPI 엔드포인트 모음."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from app.db import async_session
from app.models import (
    Title, Episode, TargetVersion, FindingRow, Character, Relationship, Segment,
    SttCorrection, ExportRow,
)
from app.core.export import assemble_final_srt, compute_stats, safety_net_check
from app.core.requery import requery_finding, RequeryNotSupportedError
from app.core.uploads import (
    save_upload, UnsupportedFileType, VIDEO_EXTENSIONS, SRT_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_ROOT,
)
from app.language_profiles.loader import load_profile, list_profiles
from app.knowledge.loader import load_knowledge
from app.providers.base import get_provider
from app.repositories import get_findings as repo_get_findings, delete_target_version_results
from app.background import analyze_and_save, extract_chart_and_save


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


class TitleIn(BaseModel):
    name: str
    type: str


class EpisodeIn(BaseModel):
    episode_no: int | None = None
    video_path: str


class TargetVersionIn(BaseModel):
    target_language: str
    variant: str


@app.get("/language-profiles")
async def get_language_profiles():
    return list_profiles()


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


class ChartImageIn(BaseModel):
    image_path: str


@app.post("/titles/{title_id}/chart-image")
async def attach_chart_image(title_id: str, payload: ChartImageIn, request: Request):
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


@app.get("/titles")
async def list_titles():
    async with async_session() as session:
        rows = (await session.execute(select(Title))).scalars().all()
        return [
            {"id": t.id, "name": t.name, "type": t.type,
             "chart_extraction_status": t.chart_extraction_status}
            for t in rows
        ]


@app.get("/titles/{title_id}")
async def get_title(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        chart_image_url = None
        if title.chart_image_path:
            chart_path = Path(title.chart_image_path)
            chart_dir = MEDIA_ROOT / "chart_image"
            if chart_path.is_relative_to(chart_dir):
                chart_image_url = f"/media/chart_image/{chart_path.relative_to(chart_dir)}"
        return {
            "id": title.id, "name": title.name, "type": title.type,
            "chart_extraction_status": title.chart_extraction_status,
            "chart_extraction_error": title.chart_extraction_error,
            "chart_image_url": chart_image_url,
        }


@app.get("/titles/{title_id}/characters")
async def list_title_characters(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        rows = (await session.execute(
            select(Character).where(Character.title_id == title_id)
        )).scalars().all()
        return [
            {"id": c.id, "label": c.label, "confirmed_gender": c.confirmed_gender,
             "suggested_gender": c.suggested_gender, "source": c.source}
            for c in rows
        ]


@app.get("/titles/{title_id}/relationships")
async def list_title_relationships(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        rows = (await session.execute(
            select(Relationship).where(Relationship.title_id == title_id)
        )).scalars().all()
        result = []
        for r in rows:
            speaker = await session.get(Character, r.speaker_character_id)
            addressee = await session.get(Character, r.addressee_character_id)
            result.append({
                "id": r.id,
                "speaker_character_id": r.speaker_character_id,
                "addressee_character_id": r.addressee_character_id,
                "speaker_label": speaker.label if speaker else None,
                "addressee_label": addressee.label if addressee else None,
                "relationship_type": r.relationship_type,
                "confirmed_formality_level": r.confirmed_formality_level,
            })
        return result


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


@app.get("/target-versions/{target_version_id}")
async def get_target_version(target_version_id: str):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        video_proxy_url = (
            f"/media/video_proxy/{Path(tv.video_proxy_path).relative_to(MEDIA_ROOT / 'video_proxy')}"
            if tv.video_proxy_path else None
        )
        return {"id": tv.id, "status": tv.status, "error_message": tv.error_message,
                "video_proxy_url": video_proxy_url, "warnings": tv.warnings or []}


class RunAnalysisIn(BaseModel):
    target_srt_path: str


@app.post("/target-versions/{target_version_id}/run-analysis")
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


@app.get("/target-versions/{target_version_id}/findings")
async def list_findings(target_version_id: str):
    async with async_session() as session:
        rows = await repo_get_findings(session, target_version_id)
        return [
            {"id": r.id, "segment_id": r.segment_id, "category": r.category,
             "description": r.description, "original_text": r.original_text,
             "suggested_text": r.suggested_text, "status": r.status,
             "model": r.model, "final_text": r.final_text}
            for r in rows
        ]


@app.get("/target-versions/{target_version_id}/segments")
async def list_segments(target_version_id: str):
    async with async_session() as session:
        rows = (await session.execute(
            select(Segment).where(Segment.target_version_id == target_version_id)
            .order_by(Segment.index)
        )).scalars().all()
        return [
            {"id": s.id, "start": s.start, "end": s.end,
             "korean_text": s.korean_text, "target_text": s.target_text}
            for s in rows
        ]


@app.get("/target-versions/{target_version_id}/characters")
async def list_characters(target_version_id: str):
    """인물은 target_version이 아니라 title 단위로 공유된다 (Task 18 confirm-gender와
    동일한 전역 제약: 같은 작품의 에피소드/언어 전반에서 재사용). target_version_id →
    episode_id → title_id 체인을 따라가 해당 title의 인물 목록을 반환한다."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        rows = (await session.execute(
            select(Character).where(Character.title_id == episode.title_id)
        )).scalars().all()
        return [
            {"id": c.id, "label": c.label, "confirmed_gender": c.confirmed_gender}
            for c in rows
        ]


@app.get("/target-versions/{target_version_id}/relationships")
async def list_relationships(target_version_id: str):
    """관계도 인물과 마찬가지로 title 단위로 공유된다. 화자/상대 인물의 라벨을 함께
    내려줘야 검수자가 관계를 식별할 수 있으므로(관계 ID만으로는 누구와 누구의 관계인지
    알 수 없음), 각 관계마다 Character를 조회해 라벨을 붙인다."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        rows = (await session.execute(
            select(Relationship).where(Relationship.title_id == episode.title_id)
        )).scalars().all()
        result = []
        for r in rows:
            speaker = await session.get(Character, r.speaker_character_id)
            addressee = await session.get(Character, r.addressee_character_id)
            result.append({
                "id": r.id,
                "speaker_character_id": r.speaker_character_id,
                "addressee_character_id": r.addressee_character_id,
                "speaker_label": speaker.label if speaker else None,
                "addressee_label": addressee.label if addressee else None,
                "confirmed_formality_level": r.confirmed_formality_level,
            })
        return result


class ReviewActionIn(BaseModel):
    action: Literal["approved", "rejected", "modified"]
    reviewer_name: str
    final_text: str = ""


@app.post("/findings/{finding_id}/review-action")
async def review_action(finding_id: str, payload: ReviewActionIn):
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        finding.status = payload.action
        finding.reviewer_name = payload.reviewer_name
        finding.reviewed_at = datetime.now(timezone.utc)
        if payload.action == "modified":
            finding.final_text = payload.final_text
        elif payload.action == "approved":
            finding.final_text = finding.suggested_text
        await session.commit()
        return {"id": finding.id, "status": finding.status, "final_text": finding.final_text}


class RequeryIn(BaseModel):
    instruction: str
    reviewer_name: str


@app.post("/findings/{finding_id}/requery")
async def requery(finding_id: str, payload: RequeryIn):
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        segment = await session.get(Segment, finding.segment_id)
        if segment is None:
            raise HTTPException(404, "segment not found")
        tv = await session.get(TargetVersion, finding.target_version_id)
        profile = load_profile(tv.target_language, tv.variant) if tv else {}

        provider = get_provider()
        knowledge = load_knowledge()
        try:
            new_suggested_text = await requery_finding(
                finding, segment, payload.instruction, provider, knowledge, profile)
        except RequeryNotSupportedError as exc:
            raise HTTPException(400, str(exc))

        finding.suggested_text = new_suggested_text
        finding.status = "pending"
        finding.description = f"[다시 질문: {payload.instruction}] {finding.description}"
        await session.commit()
        return {"id": finding.id, "status": finding.status, "suggested_text": finding.suggested_text}


class ConfirmGenderIn(BaseModel):
    gender: Literal["male", "female"]


class ConfirmFormalityIn(BaseModel):
    formality_level: Literal["formal", "informal"]


@app.post("/characters/{character_id}/confirm-gender")
async def confirm_gender(character_id: str, payload: ConfirmGenderIn):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        char.confirmed_gender = payload.gender
        await session.commit()
        return {"id": char.id, "confirmed_gender": char.confirmed_gender}


@app.post("/relationships/{relationship_id}/confirm-formality")
async def confirm_formality(relationship_id: str, payload: ConfirmFormalityIn):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        rel.confirmed_formality_level = payload.formality_level
        await session.commit()
        return {"id": rel.id, "confirmed_formality_level": rel.confirmed_formality_level}


class CorrectSttIn(BaseModel):
    corrected_text: str
    reviewer_name: str


@app.post("/segments/{segment_id}/correct-stt")
async def correct_stt(segment_id: str, payload: CorrectSttIn):
    """STT 오타를 수정하면 해당 구간만 재분석 대상으로 표시한다 (design §7).
    재분석 자체(translation_review 재호출)는 별도 배치/트리거로 수행하며 이
    엔드포인트는 텍스트 교정과 감사 기록만 담당한다."""
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        session.add(SttCorrection(
            segment_id=segment_id, original_text=seg.korean_text,
            corrected_text=payload.corrected_text, reviewer_name=payload.reviewer_name,
        ))
        seg.korean_text = payload.corrected_text
        await session.commit()
        return {"id": seg.id, "korean_text": seg.korean_text}


@app.get("/target-versions/{target_version_id}/export")
async def export_target_version(target_version_id: str):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        # export는 저장 순서(index)가 아니라 타임코드 순으로 내보낸다 —
        # alignment.align()이 짝을 못 찾은 대상언어 세그먼트를 목록 뒤에 붙이므로
        # index 순서는 실제 재생 순서와 다를 수 있다.
        seg_rows = (await session.execute(
            select(Segment).where(Segment.target_version_id == target_version_id)
            .order_by(Segment.start)
        )).scalars().all()
        finding_rows = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == target_version_id)
        )).scalars().all()

    segments = [{"id": s.id, "start": s.start, "end": s.end, "text": s.target_text} for s in seg_rows]
    # reviewed_at도 함께 넘긴다: 같은 세그먼트에 자동보정과 검수자 판단이 동시에
    # 걸린 경우 어느 쪽이 최종 텍스트가 되는지 결정하는 데 쓰인다 (검수자 우선).
    findings = [{"segment_id": f.segment_id, "status": f.status,
                 "final_text": f.final_text, "reviewed_at": f.reviewed_at}
                for f in finding_rows]
    srt = assemble_final_srt(segments, findings)
    stats = compute_stats(findings)
    # 안전망 (design §5-1의 3번 지점): assemble_final_srt와 동일한 최종 텍스트를
    # 대상으로 줄 길이를 마지막으로 한 번 더 검사한다. 위반이 있어도 export
    # 자체는 막지 않고 참고용 경고로만 응답에 포함한다 (non-blocking).
    warnings = safety_net_check(segments, findings)

    # export 이력/감사 기록 (exports 테이블). 응답으로 내려준 통계와 정확히 같은
    # 값을 남긴다.
    async with async_session() as session:
        session.add(ExportRow(
            target_version_id=target_version_id,
            finding_count=stats.finding_count,
            reflection_rate=stats.reflection_rate,
        ))
        tv = await session.get(TargetVersion, target_version_id)
        if tv is not None and tv.video_proxy_path:
            Path(tv.video_proxy_path).unlink(missing_ok=True)
            tv.video_proxy_path = None
        await session.commit()

    return {
        "srt": srt,
        "stats": stats.model_dump(),
        "format_warnings": [w.model_dump() for w in warnings],
    }


@app.post("/uploads/video")
async def upload_video(file: UploadFile = File(...)):
    try:
        path = await save_upload("video", file.filename, file.read, VIDEO_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@app.post("/uploads/srt")
async def upload_srt(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt", file.filename, file.read, SRT_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}


@app.post("/uploads/chart-image")
async def upload_chart_image(file: UploadFile = File(...)):
    try:
        path = await save_upload("chart_image", file.filename, file.read, IMAGE_EXTENSIONS)
    except UnsupportedFileType as exc:
        raise HTTPException(400, str(exc))
    return {"path": path}
