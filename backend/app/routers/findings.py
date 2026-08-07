"""findings/segments 조회, 성별·격식 해결, 검수 액션, 재질의, STT 교정 엔드포인트."""

from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, FindingRow, Character, Relationship, Segment, SttCorrection
from app.core.requery import requery_finding, RequeryNotSupportedError
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge
from app.providers.base import get_provider
from app.repositories import get_findings as repo_get_findings

router = APIRouter()


class ResolveGenderIn(BaseModel):
    character_id: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None


class ResolveFormalityIn(BaseModel):
    relationship_id: Optional[str] = None
    formality_level: Optional[Literal["formal", "informal"]] = None


class ReviewActionIn(BaseModel):
    action: Literal["approved", "rejected", "modified"]
    reviewer_name: str
    final_text: str = ""


class RequeryIn(BaseModel):
    instruction: str
    reviewer_name: str


class CorrectSttIn(BaseModel):
    corrected_text: str
    reviewer_name: str


@router.get("/target-versions/{target_version_id}/findings")
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


@router.get("/target-versions/{target_version_id}/segments")
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


@router.get("/target-versions/{target_version_id}/flagged-segments")
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


@router.post("/segments/{segment_id}/resolve-gender")
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


@router.post("/segments/{segment_id}/resolve-formality")
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


@router.post("/findings/{finding_id}/review-action")
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


@router.post("/findings/{finding_id}/requery")
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


@router.post("/segments/{segment_id}/correct-stt")
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
