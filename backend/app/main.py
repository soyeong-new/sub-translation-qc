"""작품 등록부터 분석 실행, 검수, export까지 담당하는 FastAPI 엔드포인트 모음."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, delete, update
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
    english_srt_path: str | None = None


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
                          video_path=payload.video_path,
                          english_srt_path=payload.english_srt_path)
        session.add(episode)
        await session.commit()
        return {"id": episode.id, "title_id": title_id}


class ChartImageIn(BaseModel):
    image_path: str


def _validate_chart_image_path(image_path: str) -> None:
    """image_path가 실제로 MEDIA_ROOT/chart_image 아래를 가리키는지 확인한다
    (get_title의 chart_image_url 검증과 동일한 resolve-then-check 패턴 —
    is_relative_to는 lexical하게만 비교하므로 ".."이 섞인 경로를 resolve() 없이
    검사하면 실제로는 밖을 가리키는 경로도 통과시킬 수 있다). 클라이언트가
    임의의 경로(예: /etc/passwd)를 넘겨 백그라운드 추출이 그 파일을 열어
    Anthropic API로 전송해버리는 것을 막는다."""
    chart_dir = MEDIA_ROOT / "chart_image"
    try:
        resolved_path = Path(image_path).resolve()
        resolved_dir = chart_dir.resolve()
        if not resolved_path.is_relative_to(resolved_dir):
            raise HTTPException(400, "유효하지 않은 이미지 경로입니다.")
    except ValueError:
        # 다른 드라이브(Windows) 등 방어적 예외 상황도 무효 처리한다.
        raise HTTPException(400, "유효하지 않은 이미지 경로입니다.")


@app.post("/titles/{title_id}/chart-image")
async def attach_chart_image(title_id: str, payload: ChartImageIn, request: Request):
    _validate_chart_image_path(payload.image_path)
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


class CreateCharacterIn(BaseModel):
    label: str
    suggested_gender: Optional[Literal["male", "female"]] = None


@app.post("/titles/{title_id}/characters")
async def create_character(title_id: str, payload: CreateCharacterIn):
    label = payload.label.strip()
    if not label:
        raise HTTPException(400, "인물 이름은 비어 있을 수 없습니다.")
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        # create_relationship의 get_or_create와 동일한 패턴: 같은 title 안에서
        # label이 중복되는 Character가 생기면, label로 조회하는 다른 코드 경로
        # (create_relationship, save_chart_extraction_result,
        # _save_characters_and_relationships)가 어느 행에 묶일지 예측할 수 없게
        # 된다. 이미 같은 이름이 있으면 새로 만들지 않고 그 행을 재사용한다 —
        # 프런트엔드에 409 처리 코드가 없으므로 UI의 "인물 추가"가 그대로
        # idempotent하게 동작하게 한다.
        existing = (await session.execute(
            select(Character).where(Character.title_id == title_id, Character.label == label)
        )).scalars().first()
        if existing is not None:
            if payload.suggested_gender is not None:
                existing.suggested_gender = payload.suggested_gender
                await session.commit()
            return {"id": existing.id, "label": existing.label,
                    "suggested_gender": existing.suggested_gender}
        char = Character(title_id=title_id, label=label,
                         suggested_gender=payload.suggested_gender, source="manual")
        session.add(char)
        await session.commit()
        return {"id": char.id, "label": char.label, "suggested_gender": char.suggested_gender}


class UpdateCharacterIn(BaseModel):
    label: Optional[str] = None
    suggested_gender: Optional[Literal["male", "female"]] = None


@app.patch("/characters/{character_id}")
async def update_character(character_id: str, payload: UpdateCharacterIn):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        if payload.label is not None:
            label = payload.label.strip()
            if not label:
                raise HTTPException(400, "인물 이름은 비어 있을 수 없습니다.")
            char.label = label
        if payload.suggested_gender is not None:
            char.suggested_gender = payload.suggested_gender
        await session.commit()
        return {"id": char.id, "label": char.label, "suggested_gender": char.suggested_gender}


@app.delete("/characters/{character_id}")
async def delete_character(character_id: str):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        # Segment.resolved_character_id도 characters.id를 하드 FK로 참조한다
        # (ondelete 없음) — 이 인물로 해결된 세그먼트가 있으면 그 링크부터
        # 끊어야 한다. 세그먼트 자체나 다른 필드는 그대로 두고 resolved_character_id만
        # NULL로 되돌린다 — "해결된 대상이 사라졌으니 다시 미해결 상태로" 되는 게
        # 맞는 동작이다(gender_check_needed 등 플래그는 그대로 유지돼 다시 검수 대상이 됨).
        await session.execute(
            update(Segment).where(Segment.resolved_character_id == character_id)
            .values(resolved_character_id=None)
        )
        # 이 인물이 관련된 Relationship들도 곧 지워질 텐데, 그 관계로 해결된
        # 세그먼트가 있다면 Segment.resolved_relationship_id가 매달린 채 남아
        # 같은 FK 문제를 일으킨다 — 관계를 지우기 전에 먼저 링크를 끊는다.
        await session.execute(
            update(Segment).where(
                Segment.resolved_relationship_id.in_(
                    select(Relationship.id).where(
                        (Relationship.speaker_character_id == character_id) |
                        (Relationship.addressee_character_id == character_id)
                    )
                )
            ).values(resolved_relationship_id=None)
        )
        # Relationship이 characters.id를 하드 FK로 참조하므로(ondelete 없음),
        # 인물을 지우기 전에 그 인물이 관련된 관계부터 지워야 한다.
        await session.execute(
            delete(Relationship).where(
                (Relationship.speaker_character_id == character_id) |
                (Relationship.addressee_character_id == character_id)
            )
        )
        await session.delete(char)
        await session.commit()
        return {"id": character_id, "deleted": True}


class CreateRelationshipIn(BaseModel):
    speaker_label: str
    addressee_label: str
    relationship_type: Optional[str] = None


@app.post("/titles/{title_id}/relationships")
async def create_relationship(title_id: str, payload: CreateRelationshipIn):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")

        async def get_or_create(label: str) -> Character:
            existing = (await session.execute(
                select(Character).where(Character.title_id == title_id, Character.label == label)
            )).scalars().first()
            if existing is None:
                existing = Character(title_id=title_id, label=label, source="manual")
                session.add(existing)
                await session.flush()
            return existing

        speaker = await get_or_create(payload.speaker_label)
        addressee = await get_or_create(payload.addressee_label)
        rel = Relationship(title_id=title_id, speaker_character_id=speaker.id,
                           addressee_character_id=addressee.id,
                           relationship_type=payload.relationship_type)
        session.add(rel)
        await session.commit()
        return {"id": rel.id, "speaker_label": speaker.label, "addressee_label": addressee.label,
                "relationship_type": rel.relationship_type}


class UpdateRelationshipIn(BaseModel):
    relationship_type: Optional[str] = None


@app.patch("/relationships/{relationship_id}")
async def update_relationship(relationship_id: str, payload: UpdateRelationshipIn):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        if payload.relationship_type is not None:
            rel.relationship_type = payload.relationship_type
        await session.commit()
        return {"id": rel.id, "relationship_type": rel.relationship_type}


@app.delete("/relationships/{relationship_id}")
async def delete_relationship(relationship_id: str):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        # Segment.resolved_relationship_id가 relationships.id를 하드 FK로
        # 참조하므로(ondelete 없음), 이 관계로 해결된 세그먼트가 있으면 링크부터
        # 끊어야 한다 — delete_character의 동일한 패턴 참고.
        await session.execute(
            update(Segment).where(Segment.resolved_relationship_id == relationship_id)
            .values(resolved_relationship_id=None)
        )
        await session.delete(rel)
        await session.commit()
        return {"id": relationship_id, "deleted": True}


@app.post("/titles/{title_id}/chart/confirm")
async def confirm_chart(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        title.chart_extraction_status = "confirmed"
        await session.commit()
        return {"id": title.id, "chart_extraction_status": title.chart_extraction_status}


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
        episode = await session.get(Episode, tv.episode_id)
        video_proxy_url = (
            f"/media/video_proxy/{Path(tv.video_proxy_path).relative_to(MEDIA_ROOT / 'video_proxy')}"
            if tv.video_proxy_path else None
        )
        return {"id": tv.id, "status": tv.status, "error_message": tv.error_message,
                "video_proxy_url": video_proxy_url, "warnings": tv.warnings or [],
                "title_id": episode.title_id if episode else None}


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


@app.get("/target-versions/{target_version_id}/flagged-segments")
async def list_flagged_segments(target_version_id: str):
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        rows = (await session.execute(
            select(Segment).where(
                Segment.target_version_id == target_version_id,
                (Segment.gender_check_needed == True) | (Segment.formality_check_needed == True),  # noqa: E712
            ).order_by(Segment.index)
        )).scalars().all()
        return [
            {"id": s.id, "start": s.start, "end": s.end,
             "korean_text": s.korean_text, "target_text": s.target_text,
             "gender_check_needed": s.gender_check_needed,
             "formality_check_needed": s.formality_check_needed,
             "resolved_character_id": s.resolved_character_id,
             "resolved_gender_raw": s.resolved_gender_raw,
             "resolved_relationship_id": s.resolved_relationship_id,
             "resolved_formality_raw": s.resolved_formality_raw,
             "gender_anchor_candidates": s.gender_anchor_candidates or [],
             "formality_anchor_candidates": s.formality_anchor_candidates or [],
             "english_pronoun_hint": s.english_pronoun_hint}
            for s in rows
        ]


class ResolveGenderIn(BaseModel):
    character_id: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None


@app.post("/segments/{segment_id}/resolve-gender")
async def resolve_gender(segment_id: str, payload: ResolveGenderIn):
    if bool(payload.character_id) == bool(payload.gender):
        raise HTTPException(400, "character_id와 gender 중 정확히 하나만 지정해야 합니다.")
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        if payload.character_id:
            # 검수 화면의 앵커 후보 버튼은 분석 시점 스냅샷이므로, 그 사이 인물이
            # 지워졌으면 여기서 존재를 먼저 확인해 깔끔한 400으로 막아야 한다 —
            # 그냥 저장하면 commit 시점에 처리되지 않은 IntegrityError(500)가 난다.
            char = await session.get(Character, payload.character_id)
            if char is None:
                raise HTTPException(400, "존재하지 않는 인물입니다.")
            seg.resolved_character_id = payload.character_id
            seg.resolved_gender_raw = None
        else:
            seg.resolved_gender_raw = payload.gender
            seg.resolved_character_id = None
        await session.commit()
        return {"id": seg.id, "resolved_character_id": seg.resolved_character_id,
                "resolved_gender_raw": seg.resolved_gender_raw}


class ResolveFormalityIn(BaseModel):
    relationship_id: Optional[str] = None
    formality_level: Optional[Literal["formal", "informal"]] = None


@app.post("/segments/{segment_id}/resolve-formality")
async def resolve_formality(segment_id: str, payload: ResolveFormalityIn):
    if bool(payload.relationship_id) == bool(payload.formality_level):
        raise HTTPException(400, "relationship_id와 formality_level 중 정확히 하나만 지정해야 합니다.")
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        if payload.relationship_id:
            # resolve_gender와 동일한 이유로, 저장 전에 관계가 실제로 존재하는지
            # 먼저 확인한다.
            rel = await session.get(Relationship, payload.relationship_id)
            if rel is None:
                raise HTTPException(400, "존재하지 않는 관계입니다.")
            seg.resolved_relationship_id = payload.relationship_id
            seg.resolved_formality_raw = None
        else:
            seg.resolved_formality_raw = payload.formality_level
            seg.resolved_relationship_id = None
        await session.commit()
        return {"id": seg.id, "resolved_relationship_id": seg.resolved_relationship_id,
                "resolved_formality_raw": seg.resolved_formality_raw}


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

        resolved_character = None
        if segment.resolved_character_id:
            char = await session.get(Character, segment.resolved_character_id)
            if char is not None:
                resolved_character = {"id": char.id, "label": char.label,
                                      "confirmed_gender": char.confirmed_gender}
        resolved_relationship = None
        if segment.resolved_relationship_id:
            rel = await session.get(Relationship, segment.resolved_relationship_id)
            if rel is not None:
                resolved_relationship = {
                    "id": rel.id, "confirmed_formality_level": rel.confirmed_formality_level}

        try:
            new_suggested_text = await requery_finding(
                finding, segment, payload.instruction, provider, knowledge, profile,
                resolved_character=resolved_character, resolved_relationship=resolved_relationship)
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


@app.post("/uploads/srt-en")
async def upload_srt_en(file: UploadFile = File(...)):
    try:
        path = await save_upload("srt_en", file.filename, file.read, SRT_EXTENSIONS)
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
