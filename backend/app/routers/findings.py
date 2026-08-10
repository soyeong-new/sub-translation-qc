"""findings/segments 조회, 성별·격식 해결, 검수 액션, 재질의, STT 교정 엔드포인트."""

import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, FindingRow, Segment, SttCorrection
from app.core.requery import (
    requery_finding, reverify_segment_after_stt_correction, RequeryNotSupportedError,
)
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge
from app.providers.base import get_provider
from app.repositories import get_findings as repo_get_findings

router = APIRouter()


class ResolveGenderIn(BaseModel):
    gender: Literal["male", "female"]


class ResolveFormalityIn(BaseModel):
    formality_level: Literal["formal", "informal"]


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
             "resolved_gender_raw": s.resolved_gender_raw,
             "resolved_formality_raw": s.resolved_formality_raw,
             "english_pronoun_hint": s.english_pronoun_hint}
            for s in rows
        ]


@router.post("/segments/{segment_id}/resolve-gender")
async def resolve_gender(segment_id: str, payload: ResolveGenderIn):
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.resolved_gender_raw = payload.gender
        await session.commit()
        return {"id": seg.id, "resolved_gender_raw": seg.resolved_gender_raw}


@router.post("/segments/{segment_id}/resolve-formality")
async def resolve_formality(segment_id: str, payload: ResolveFormalityIn):
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.resolved_formality_raw = payload.formality_level
        await session.commit()
        return {"id": seg.id, "resolved_formality_raw": seg.resolved_formality_raw}


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


@router.post("/segments/{segment_id}/correct-stt")
async def correct_stt(segment_id: str, payload: CorrectSttIn):
    """STT 오타를 수정하면, 새 원문 기준으로 그 줄의 번역이 여전히 맞는지
    GPT 하나로만 가볍게 재검증한다(design §STT 재검증은 가볍게 — 이중
    독립검증까지는 안 함). 문제가 있으면 pending finding을 새로 만들어
    검수자가 리뷰 화면에서 승인/거절/수정하게 한다."""
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        session.add(SttCorrection(
            segment_id=segment_id, original_text=seg.korean_text,
            corrected_text=payload.corrected_text, reviewer_name=payload.reviewer_name,
        ))
        seg.korean_text = payload.corrected_text

        tv = await session.get(TargetVersion, seg.target_version_id)
        profile = load_profile(tv.target_language, tv.variant) if tv else {}
        provider = get_provider()
        knowledge = load_knowledge()
        correction = await reverify_segment_after_stt_correction(seg, provider, knowledge, profile)

        new_finding = None
        if correction and correction["corrected_text"] != seg.target_text:
            # uuid를 쓴다 — 같은 세그먼트를 여러 번 고칠 수 있어(segment_id,
            # category) 조합이 겹칠 수 있고, 그러면 두 번째 저장에서 PK 충돌로
            # 재검증 결과가 통째로 유실된다.
            finding = FindingRow(
                id=f"{segment_id}_stt_recheck_{uuid.uuid4()}",
                target_version_id=seg.target_version_id, segment_id=segment_id,
                category=correction["category"],
                description=f"[STT 수정 후 재검증] {correction['description']}",
                original_text=seg.target_text, suggested_text=correction["corrected_text"],
                confidence=1.0, source="llm", model="gpt", status="pending",
            )
            session.add(finding)
            new_finding = {"id": finding.id, "category": finding.category,
                           "suggested_text": finding.suggested_text}

        await session.commit()
        return {"id": seg.id, "korean_text": seg.korean_text, "new_finding": new_finding}
