"""findings/segments 조회, 성별·격식 해결, 검수 액션, 재질의, STT 교정 엔드포인트."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, FindingRow, Segment, SttCorrection
from app.core.grammar_necessity import check_grammar_necessity
from app.core.requery import (
    requery_finding, reverify_segment_after_stt_correction, RequeryNotSupportedError,
    apply_resolved_gender_to_text, flag_new_gender_ambiguity, gloss_new_gender_words,
    reapply_gender_to_pending_findings,
)
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES, violates_line_length
from app.core.safety_net import enforce_line_length
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge
from app.providers.base import get_provider
from app.repositories import (
    get_findings as repo_get_findings, get_findings_for_segment,
)

logger = logging.getLogger(__name__)

_LINE_LENGTH_ERROR = (
    f"최종 텍스트가 자막 글자수 제약(줄당 {MAX_LINE_CHARS}자 이내, 최대 {MAX_LINES}줄)을 "
    "넘었습니다 — 직접 입력한 문구는 자동으로 줄이지 않으니, 줄여서 다시 시도해주세요."
)

router = APIRouter()


class ResolveGenderIn(BaseModel):
    # not_applicable: 성별 표시가 걸렸지만 실제로는 사람이 아니라 사물/상황을
    # 가리키는 단어인 경우(예: "caro"=비싸다) — 검수자가 뜻을 보고 사람과
    # 무관하다고 판단했을 때 고른다. male/female처럼 resolved_gender_raw에
    # 저장되지만, AI 검증 프롬프트에는 실제 성별 값처럼 전달되지 않는다
    # (pipeline._build_resolved_registers/resolved_registers_from_segments가
    # not_applicable을 걸러낸다).
    gender: Literal["male", "female", "not_applicable"]


class ResolveGenderGroupIn(BaseModel):
    # 한 줄에 성별이 다른 인물이 둘 이상 있을 때, 그중 한 인물(group_index로
    # 지정)의 성별만 답한다 — ResolveGenderIn과 값 종류는 같지만 대상이
    # 줄 전체가 아니라 그 줄 안의 특정 인물 하나다.
    group_index: int
    gender: Literal["male", "female", "not_applicable"]


class ResolveFormalityIn(BaseModel):
    formality_level: Literal["formal", "informal"]


class ExcludeSegmentIn(BaseModel):
    excluded: bool


class ReviewActionIn(BaseModel):
    action: Literal["approved", "rejected", "modified"]
    reviewer_name: str
    final_text: str = ""


class PickFindingIn(BaseModel):
    # Claude/GPT가 같은 세그먼트에 대해 의견이 갈렸을 때(각자 pending finding
    # 하나씩) 검수자가 그중 하나를 고르는 액션 — 고른 쪽은 승인(또는 final_text가
    # 있으면 그 문구로 수정), 짝(other_finding_id)은 자동으로 거부된다.
    reviewer_name: str
    other_finding_id: str
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
             "korean_text": s.korean_text, "target_text": s.target_text,
             # ReviewView가 STT 재검증으로 새로 생긴 성별 미확정 케이스를
             # 인라인으로 물어봐야 하는지 판단하는 데 필요하다(FlaggedSegmentStepper의
             # isGenderResolved와 같은 필드).
             "gender_check_needed": s.gender_check_needed,
             "resolved_gender_raw": s.resolved_gender_raw,
             "resolved_gender_groups_raw": s.resolved_gender_groups_raw,
             "excluded": s.excluded}
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
             "resolved_gender_groups_raw": s.resolved_gender_groups_raw,
             "excluded": s.excluded}
            for s in rows
        ]


@router.post("/segments/{segment_id}/resolve-gender")
async def resolve_gender(segment_id: str, payload: ResolveGenderIn):
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.resolved_gender_raw = payload.gender
        # STT 재검증이 성별 확인을 기다리며 만들어둔 pending finding이
        # 있으면, 방금 답한 값을 그 제안문구에도 반영한다.
        tv = await session.get(TargetVersion, seg.target_version_id)
        if tv is not None:
            profile = load_profile(tv.target_language, tv.variant)
            await reapply_gender_to_pending_findings(session, seg, profile.get("language"))
        await session.commit()
        return {"id": seg.id, "resolved_gender_raw": seg.resolved_gender_raw}


@router.post("/segments/{segment_id}/resolve-gender-group")
async def resolve_gender_group(segment_id: str, payload: ResolveGenderGroupIn):
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        groups = seg.resolved_gender_groups_raw
        if not groups or not (0 <= payload.group_index < len(groups)):
            raise HTTPException(400, "잘못된 인물 그룹 인덱스입니다")
        # JSON 컬럼은 원소를 제자리에서(in-place) 바꿔도 세션이 변경을
        # 감지하지 못한다(SQLAlchemy JSON은 MutableList가 아님) — 새
        # 리스트를 만들어 컬럼 자체를 통째로 재할당해야 UPDATE가 나간다.
        new_groups = [dict(g) for g in groups]
        new_groups[payload.group_index]["gender"] = payload.gender
        seg.resolved_gender_groups_raw = new_groups

        tv = await session.get(TargetVersion, seg.target_version_id)
        # STT 재검증이 성별 확인을 기다리며 만들어둔 pending finding이
        # 있으면, 방금 답한 값을 그 제안문구에도 반영한다.
        if tv is not None:
            profile = load_profile(tv.target_language, tv.variant)
            await reapply_gender_to_pending_findings(session, seg, profile.get("language"))
        await session.commit()
        return {"id": seg.id, "resolved_gender_groups_raw": seg.resolved_gender_groups_raw}


@router.post("/segments/{segment_id}/resolve-formality")
async def resolve_formality(segment_id: str, payload: ResolveFormalityIn):
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.resolved_formality_raw = payload.formality_level
        await session.commit()
        return {"id": seg.id, "resolved_formality_raw": seg.resolved_formality_raw}


@router.post("/segments/{segment_id}/exclude")
async def exclude_segment(segment_id: str, payload: ExcludeSegmentIn):
    """검수자가 겹치는 짝이 없는 반쪽짜리 Segment를 최종 자막에서 뺄지
    결정한다(design 2026-08-13-korean-srt-cue-based-segmentation-design.md
    §신규: 제외 표시). 대상언어 텍스트가 있는 Segment를 제외하면 최종 SRT
    에서 그 구간이 통째로 빠진다(export.py의 assemble_final_srt). 대상언어가
    없는 Segment는 원래도 최종 SRT에 안 나가므로, 이 값은 검수 화면에서
    "처리됨" 표시 용도로만 쓰인다."""
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.excluded = payload.excluded
        await session.commit()
        return {"id": seg.id, "excluded": seg.excluded}


@router.post("/findings/{finding_id}/review-action")
async def review_action(finding_id: str, payload: ReviewActionIn):
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        # 검수자가 직접 타이핑한 문구(수정)는 시스템이 임의로 잘라버리지
        # 않는다 — 대신 제약을 넘으면 저장 자체를 막고 직접 줄이게 한다.
        if payload.action == "modified" and violates_line_length(payload.final_text):
            raise HTTPException(400, _LINE_LENGTH_ERROR)
        finding.status = payload.action
        finding.reviewer_name = payload.reviewer_name
        finding.reviewed_at = datetime.now(timezone.utc)
        if payload.action == "modified":
            finding.final_text = payload.final_text
        elif payload.action == "approved":
            # AI 제안은 검수자의 문구가 아니라서, 제약을 넘으면 자동으로
            # 줄인다(승인 시점까지는 S4 안전망을 안 거치는 pending 제안이라
            # 여기서 처음 걸러진다).
            finding.final_text, _ = await enforce_line_length(finding.suggested_text, get_provider())
        await session.commit()
        return {"id": finding.id, "status": finding.status, "final_text": finding.final_text}


@router.post("/findings/{finding_id}/pick")
async def pick_finding(finding_id: str, payload: PickFindingIn):
    """Claude/GPT 의견이 갈린 두 후보 중 하나를 선택한다. 한 세그먼트에 두
    finding이 모두 "승인"으로 남으면 export.py의 _final_text_by_segment가
    임의 순서로 하나를 골라 최종 텍스트가 뭐가 될지 불확실해지는 문제가
    있었다 — 승인/거부를 하나의 트랜잭션으로 같이 처리해 항상 정확히 하나만
    최종 텍스트를 갖게 만든다."""
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        other = await session.get(FindingRow, payload.other_finding_id)
        if finding is None or other is None:
            raise HTTPException(404, "finding not found")
        # 검수자가 직접 고친 문구(수정해서 채택)는 임의로 잘라버리지 않고
        # 제약을 넘으면 저장을 막는다 — review-action의 "modified"와 동일한
        # 원칙.
        if payload.final_text and violates_line_length(payload.final_text):
            raise HTTPException(400, _LINE_LENGTH_ERROR)
        now = datetime.now(timezone.utc)

        if payload.final_text:
            finding.status = "modified"
            finding.final_text = payload.final_text
        else:
            finding.status = "approved"
            # 그대로 채택한 AI 제안은 제약을 넘으면 자동으로 줄인다.
            finding.final_text, _ = await enforce_line_length(finding.suggested_text, get_provider())
        finding.reviewer_name = payload.reviewer_name
        finding.reviewed_at = now

        other.status = "rejected"
        other.final_text = ""
        other.reviewer_name = payload.reviewer_name
        other.reviewed_at = now

        await session.commit()
        return {
            "picked": {"id": finding.id, "status": finding.status, "final_text": finding.final_text},
            "rejected": {"id": other.id, "status": other.status, "final_text": other.final_text},
        }


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

        # 재질문도 S2와 같은 문제를 안고 있다 — AI가 문장을 다시 쓰면서
        # 이미 확정된 성별을 건드릴 수 있다. correct_stt와 동일하게 재적용한다.
        new_suggested_text = await asyncio.to_thread(
            apply_resolved_gender_to_text, segment, new_suggested_text, profile.get("language"))
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

        if correction:
            # GPT가 새로 만든 제안문구는 1차 검수 때 이미 확정된 성별을
            # 전혀 모른다 — 여기서 재적용하지 않으면 "cansado/a"처럼 성별
            # 미확정 표기가 그대로 새어나간다. 재적용 후에도 여전히 성별
            # 확인이 필요하면(1차 검수 때는 없던 성별 표시 단어가 새로 생긴
            # 경우) 세그먼트를 다시 미확인 상태로 표시해 리뷰 화면에서
            # 사람에게 물어보게 한다.
            language = profile.get("language")
            fixed_text = await asyncio.to_thread(
                apply_resolved_gender_to_text, seg, correction["corrected_text"], language)
            remaining_flags = await asyncio.to_thread(
                check_grammar_necessity,
                [{"id": segment_id, "target_text": fixed_text, "korean_text": seg.korean_text}], profile)
            if await flag_new_gender_ambiguity(seg, remaining_flags[0], provider, profile):
                # 새로 생긴 그룹은 뜻풀이가 없다 — 스페인어를 모르는 검수자가
                # "caro"만 보고는 사람 얘기인지조차 못 가른다. 실패해도(네트워크
                # 등) STT 재검증 저장 자체는 막지 않는다 — 뜻풀이는 참고용이다.
                try:
                    await gloss_new_gender_words(seg, provider, profile)
                except Exception:
                    logger.exception(
                        "새 성별 표시 단어 뜻풀이 실패, 뜻풀이 없이 계속 진행 (segment_id=%s)",
                        segment_id)
            correction["corrected_text"] = fixed_text

        new_finding = None
        if correction and correction["corrected_text"] != seg.target_text:
            # 검수자가 스페인어를 몰라도 "원본"(수정 전 문장)이 무슨 뜻인지
            # 알아야 "제안"과 비교 판단이 가능하다 — GPT의 original_meaning이
            # 원본 뜻을 이미 풀어주지만, 그건 GPT 자신의 이해일 뿐이라 실제
            # 원본 문장 자체를 기계적으로 역번역한 것도 별도로 붙인다(서로
            # 다른 방식의 교차 확인). 실패해도(네트워크 등) STT 재검증
            # 자체는 막지 않는다 — 역번역은 참고용 보조 정보다.
            original_backtranslation = None
            try:
                backtranslated = await provider.back_translate_with_claude(
                    [{"id": segment_id, "text": seg.target_text}], profile)
                if backtranslated:
                    original_backtranslation = backtranslated[0]["korean_text"]
            except Exception:
                logger.exception(
                    "원본 역번역 실패, 역번역 없이 계속 진행 (segment_id=%s)", segment_id)
            description = f"[STT 수정 후 재검증] {correction['description']}"
            original_meaning = correction.get("original_meaning")
            if original_meaning:
                description += f" (원본 뜻 참고: {original_meaning})"
            if original_backtranslation:
                description += f" (원본 한국어 역번역 참고: {original_backtranslation})"

            # 이 세그먼트에 finding이 이미 정확히 하나뿐이면(가장 흔한 경우 —
            # 이전에 승인/거부까지 끝난 상태) 새 카드를 또 만들지 않고 그
            # 카드를 그 자리에서 갱신한다 — 새로 만들면 같은 세그먼트에 대해
            # "승인됨" finding이 두 개 남을 수 있는데, export가 최종 텍스트를
            # 고를 때 reviewed_at 유무만 boolean으로 비교해서(실제 시각 순서
            # 비교 아님) 어느 쪽이 이길지 보장이 안 된다. finding이 0개거나
            # 2개 이상(의견 갈림 등 아직 안 끝난 상태)이면 어느 걸 갱신해야
            # 할지 애매하므로 안전하게 새로 만든다.
            existing = await get_findings_for_segment(session, segment_id)
            if len(existing) == 1:
                finding = existing[0]
                finding.category = correction["category"]
                finding.description = description
                finding.original_text = seg.target_text
                finding.suggested_text = correction["corrected_text"]
                finding.confidence = 1.0
                finding.source = "llm"
                finding.model = "gpt"
                finding.status = "pending"
                finding.final_text = ""
                finding.reviewer_name = ""
                finding.reviewed_at = None
            else:
                # uuid를 쓴다 — 같은 세그먼트를 여러 번 고칠 수 있어(segment_id,
                # category) 조합이 겹칠 수 있고, 그러면 두 번째 저장에서 PK
                # 충돌로 재검증 결과가 통째로 유실된다.
                finding = FindingRow(
                    id=f"{segment_id}_stt_recheck_{uuid.uuid4()}",
                    target_version_id=seg.target_version_id, segment_id=segment_id,
                    category=correction["category"],
                    description=description,
                    original_text=seg.target_text, suggested_text=correction["corrected_text"],
                    confidence=1.0, source="llm", model="gpt", status="pending",
                )
                session.add(finding)
            new_finding = {"id": finding.id, "category": finding.category,
                           "suggested_text": finding.suggested_text}

        await session.commit()
        return {"id": seg.id, "korean_text": seg.korean_text, "new_finding": new_finding}
