"""findings/segments 조회, 성별·격식 해결, 검수 액션, 재질의, STT 교정 엔드포인트."""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, FindingRow, Segment, SttCorrection, Episode
from app.core.grammar_necessity import check_grammar_necessity
from app.core.requery import (
    requery_finding, reverify_segment_after_stt_correction,
    RequeryNotSupportedError, RequeryNoResultError,
    apply_resolved_gender_to_text, flag_new_gender_ambiguity, gloss_new_gender_words,
    reapply_gender_to_pending_findings,
)
from app.core.format_rules import rewrap_line, violates_line_length, final_text_reason
from app.core.neighbor_warning import compute_neighbor_warning
from app.core.person_hint import likely_not_person
from app.core.glossary_guard import patch_missing_canonical, find_canonical_overrides
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge
from app.providers.base import get_provider
from app.repositories import (
    get_findings as repo_get_findings, get_findings_for_segment,
    get_glossary_prompt_entries, update_glossary_entry,
    split_segment_in_session, undo_split_in_session, SplitUndoError,
)

logger = logging.getLogger(__name__)

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


class RejectPairIn(BaseModel):
    # Claude/GPT 둘 다의 제안보다 원본이 낫다고 판단했을 때, 두 finding을
    # 한 트랜잭션으로 같이 거부해 원본을 유지한다 — pick과 대칭되는 액션.
    reviewer_name: str
    other_finding_id: str


class RequeryIn(BaseModel):
    instruction: str
    reviewer_name: str


class CorrectSttIn(BaseModel):
    corrected_text: str
    reviewer_name: str


class EditTargetTextIn(BaseModel):
    target_text: str
    reviewer_name: str = ""


class EditSegmentTimingIn(BaseModel):
    start: float
    end: float
    reviewer_name: str = ""


class SplitSegmentIn(BaseModel):
    # 화면에 지금 보이는 최종 텍스트(finding이 반영됐으면 그 final_text) —
    # DB의 seg.target_text는 finding 반영 전 원본일 수 있어 따로 받는다.
    target_text: str = ""


@router.get("/target-versions/{target_version_id}/findings")
async def list_findings(target_version_id: str):
    async with async_session() as session:
        rows = await repo_get_findings(session, target_version_id)
        # 이웃 큐 경고용: 영상 순서(index)로 앞뒤 세그먼트의 번역문을 찾는다.
        ordered = (await session.execute(
            select(Segment.id, Segment.target_text).where(Segment.target_version_id == target_version_id)
            .order_by(Segment.index)
        )).all()
        position = {sid: i for i, (sid, _) in enumerate(ordered)}

        def _neighbor_texts(segment_id: str):
            i = position.get(segment_id)
            if i is None:
                return None, None
            return (ordered[i - 1][1] if i > 0 else None,
                    ordered[i + 1][1] if i + 1 < len(ordered) else None)

        return [
            {"id": r.id, "segment_id": r.segment_id, "category": r.category,
             "description": r.description, "original_text": r.original_text,
             "suggested_text": r.suggested_text, "status": r.status,
             "model": r.model, "final_text": r.final_text,
             "final_text_reason": (final_text_reason(r.suggested_text, r.final_text)
                                   if r.status == "approved" else None),
             # 검수자가 실제로 review-action/pick을 눌렀는지 구분하는 값 — 이게
             # None이면 status가 "approved"여도 사람이 아니라 파이프라인이
             # 자동으로 승인한 것이다(예: Claude+GPT 합의 자동 적용, 안전망
             # 자동 축약). 프론트가 카드 테두리 색을 "검수자가 판단했는지"로만
             # 표시하려면 status가 아니라 이 값을 봐야 한다.
             "reviewed_at": r.reviewed_at,
             # 승인 전 확인용 알림(지적을 막거나 지우지 않는다): 제안이 이웃 큐에 이미 있는
             # 내용을 또 넣으면 neighbor_warning이 채워진다.
             "neighbor_warning": compute_neighbor_warning(
                 r.original_text, r.suggested_text, *_neighbor_texts(r.segment_id))}
            for r in rows
        ]


@router.get("/target-versions/{target_version_id}/segments")
async def list_segments(target_version_id: str):
    async with async_session() as session:
        rows = (await session.execute(
            select(Segment).where(Segment.target_version_id == target_version_id)
            .order_by(Segment.index, Segment.start)  # 분할로 생긴 큐는 index가 같다
        )).scalars().all()
        # 분할 쌍의 어느 쪽에서든 되돌릴 수 있게, 큐마다 "지울 새 큐" id를 정한다. 새 큐는 자기 자신,
        # 원래 큐는 자식 중 가장 앞(마지막에 분할된 것 — 분할은 늘 앞부분을 원래 큐에 남긴다).
        undo_target = {}
        for s in rows:   # rows는 (index, start) 순이라 같은 원래 큐의 자식 중 첫 번째가 남는다
            if s.split_undo_raw:
                undo_target[s.id] = s.id
                undo_target.setdefault(s.split_undo_raw["original_segment_id"], s.id)
        return [
            {"id": s.id, "start": s.start, "end": s.end,
             "korean_text": s.korean_text, "target_text": s.target_text,
             # ReviewView가 STT 재검증으로 새로 생긴 성별 미확정 케이스를
             # 인라인으로 물어봐야 하는지 판단하는 데 필요하다(FlaggedSegmentStepper의
             # isGenderResolved와 같은 필드).
             "gender_check_needed": s.gender_check_needed,
             "resolved_gender_raw": s.resolved_gender_raw,
             "resolved_gender_groups_raw": s.resolved_gender_groups_raw,
             "excluded": s.excluded,
             "can_undo_split": s.id in undo_target, "undo_split_id": undo_target.get(s.id)}
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
             # 읽을 때 계산해서 붙인다(저장하지 않음 — 이미 분석된 작품에도 적용되고, 확정 로직이 쓰는
             # suggested_gender와 섞이지 않는다). 화면이 "사람 아님" 버튼을 미리 강조하는 데만 쓴다.
             "resolved_gender_groups_raw": [
                 {**g, "likely_not_person": likely_not_person(g.get("referent"))}
                 for g in s.resolved_gender_groups_raw
             ] if s.resolved_gender_groups_raw else s.resolved_gender_groups_raw,
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
            await reapply_gender_to_pending_findings(session, seg, get_provider(), profile)
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
        new_groups[payload.group_index]["human_confirmed"] = True
        seg.resolved_gender_groups_raw = new_groups

        tv = await session.get(TargetVersion, seg.target_version_id)
        # STT 재검증이 성별 확인을 기다리며 만들어둔 pending finding이
        # 있으면, 방금 답한 값을 그 제안문구에도 반영한다.
        if tv is not None:
            profile = load_profile(tv.target_language, tv.variant)
            await reapply_gender_to_pending_findings(session, seg, get_provider(), profile)
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


async def _load_glossary_context(session, finding: FindingRow):
    """finding이 속한 세그먼트의 한국어 원문과, 그 작품·언어판에 이미
    등록된 용어집 항목(entry_id 포함)을 가져온다 — 검수 액션 시점에
    고유명사 표기 일관성을 확인/보정하는 데 쓴다."""
    segment = await session.get(Segment, finding.segment_id)
    tv = await session.get(TargetVersion, finding.target_version_id)
    episode = await session.get(Episode, tv.episode_id)
    glossary_entries = await get_glossary_prompt_entries(
        session, episode.title_id, tv.target_language, tv.variant)
    return segment.korean_text, tv.target_language, tv.variant, glossary_entries


async def _apply_glossary_override(session, finding: FindingRow, final_text: str) -> None:
    """검수자가 직접 입력한 문구(modified)가 등록된 표준 표기와 다른
    고유명사를 쓰면, 강제로 되돌리지 않고 검수자의 판단을 표준 표기로
    승격한다(자가 치유) — 최초 등록이 틀렸어도 이후 검수 한 번이면
    바로잡힌다."""
    korean_text, target_language, variant, glossary_entries = (
        await _load_glossary_context(session, finding))
    for entry_id, new_canonical in find_canonical_overrides(
            korean_text, finding.original_text, final_text, glossary_entries):
        await update_glossary_entry(
            session, entry_id, spellings={f"{target_language}_{variant}": new_canonical})


async def _line_break_rules(session, target_version_id: str) -> Optional[dict]:
    """언어판 프로파일의 줄바꿈 위치 비용 단어 목록(rewrap_line의 rules). 프로파일이
    없는 언어면 None — 구두점·길이 균형만으로 줄바꿈한다."""
    tv = await session.get(TargetVersion, target_version_id)
    try:
        return load_profile(tv.target_language, tv.variant).get("line_break") if tv else None
    except FileNotFoundError:
        return None


async def _finalize_approved_text(session, finding: FindingRow, text: str, ignore_ids=()) -> Optional[dict]:
    """승인·채택한 AI 제안(text)의 글자수 제약을 맞춰 finding.final_text에 넣는다. 줄바꿈 재배치로
    풀리면 그걸로, 안 풀리면 LLM으로 줄이지 않고 큐를 나눈다(내용 보존; 그 큐에 다른 처리 안 된
    finding이 있으면 나누지 않는다 — 그쪽이 문구를 바꿀 수 있다). 나눴으면 화면이 목록을 다시 불러오도록
    {"original", "new"}를 반환하고, 아니면 None."""
    rules = await _line_break_rules(session, finding.target_version_id)
    fitted = text if not violates_line_length(text) else rewrap_line(text, rules=rules, avoid_forbidden=True)
    finding.final_text = fitted if fitted is not None else text
    if fitted is not None:
        return None
    # 자연스럽게 끊을 자리가 없으면 큐를 나눈다. 못 나누면 어색해도 줄바꿈으로 맞춘다.
    forced = rewrap_line(text, rules=rules)
    seg = await session.get(Segment, finding.segment_id)
    if seg is None or any(f.status == "pending" and f.id != finding.id and f.id not in ignore_ids
                          for f in await get_findings_for_segment(session, seg.id)):
        return None
    new_seg = await split_segment_in_session(session, seg, text, rules)
    if new_seg is None:
        finding.final_text = forced if forced is not None else text
        return None
    await session.flush()
    finding.status, finding.final_text = "approved", seg.target_text  # 승인한 제안 그대로 + 첫 조각
    return {"original": {"id": seg.id, "start": seg.start, "end": seg.end, "target_text": seg.target_text},
            "new": {"id": new_seg.id, "start": new_seg.start, "end": new_seg.end,
                    "korean_text": new_seg.korean_text, "target_text": new_seg.target_text,
                    "can_undo_split": True}}


@router.post("/findings/{finding_id}/review-action")
async def review_action(finding_id: str, payload: ReviewActionIn):
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        # 검수자가 직접 쓴 문구(수정)는 줄바꿈·길이를 건드리지 않고 쓴 그대로 저장한다(길이 초과는 export 경고로 보인다).
        split = None
        finding.status = payload.action
        finding.reviewer_name = payload.reviewer_name
        finding.reviewed_at = datetime.now(timezone.utc)
        if payload.action == "modified":
            finding.final_text = payload.final_text
            await _apply_glossary_override(session, finding, payload.final_text)
        elif payload.action == "rejected" and finding.model == "자동재배치":
            # 자동 줄바꿈 재배치는 이미 세그먼트에 반영돼 있어서, 거부하면 원문으로 되돌린다.
            # 그 사이 세그먼트 텍스트가 바뀌었으면(분할 등) 건드리지 않는다.
            seg = await session.get(Segment, finding.segment_id)
            if seg is not None and seg.target_text == finding.suggested_text:
                seg.target_text = finding.original_text
        elif payload.action == "approved":
            # AI 제안은 검수자의 문구가 아니라서, 제약을 넘으면 자동으로
            # 줄인다(승인 시점까지는 S4 안전망을 안 거치는 pending 제안이라
            # 여기서 처음 걸러진다). 줄당 글자수 제약(42자, 최대 2줄)을 검사한다.
            # 등록된 고유명사 표준 표기가 AI 제안에서 빠졌으면(LLM 프롬프트
            # 지시만으로는 안 지켜질 수 있음) 여기서 마지막으로 강제한다.
            korean_text, _, _, glossary_entries = await _load_glossary_context(session, finding)
            patched_text = patch_missing_canonical(
                korean_text, finding.original_text, finding.suggested_text, glossary_entries)
            split = await _finalize_approved_text(session, finding, patched_text)
        await session.commit()
        return {"id": finding.id, "status": finding.status, "final_text": finding.final_text, "split": split}


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
        now = datetime.now(timezone.utc)
        split = None

        if payload.final_text:
            finding.status = "modified"
            finding.final_text = payload.final_text
            await _apply_glossary_override(session, finding, payload.final_text)
        else:
            finding.status = "approved"
            # 그대로 채택한 AI 제안은 줄당 글자수 제약(42자, 최대 2줄)을
            # 넘으면 자동으로 줄인다. 등록된 고유명사 표준 표기가 빠졌으면
            # review-action과 동일하게 여기서도 마지막으로 강제한다.
            korean_text, _, _, glossary_entries = await _load_glossary_context(session, finding)
            patched_text = patch_missing_canonical(
                korean_text, finding.original_text, finding.suggested_text, glossary_entries)
            split = await _finalize_approved_text(
                session, finding, patched_text, ignore_ids=(payload.other_finding_id,))
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
            "split": split,
        }


@router.post("/findings/{finding_id}/reject-pair")
async def reject_pair(finding_id: str, payload: RejectPairIn):
    """Claude/GPT 의견이 갈린 두 후보 중 어느 쪽도 원본보다 낫지 않다고
    판단했을 때, 원본을 그대로 유지한다. pick_finding과 대칭되는 액션으로
    — 두 finding을 한 트랜잭션으로 같이 거부해 한쪽만 거부되고 다른 한쪽은
    아직 pending으로 남는 어중간한 상태를 막는다. 거부된 finding은
    final_text가 비므로 export._final_text_by_segment가 이 세그먼트를
    건너뛰고 Segment.target_text(원본)를 그대로 최종 텍스트로 쓴다."""
    async with async_session() as session:
        finding = await session.get(FindingRow, finding_id)
        other = await session.get(FindingRow, payload.other_finding_id)
        if finding is None or other is None:
            raise HTTPException(404, "finding not found")
        now = datetime.now(timezone.utc)
        for f in (finding, other):
            f.status = "rejected"
            f.final_text = ""
            f.reviewer_name = payload.reviewer_name
            f.reviewed_at = now
        await session.commit()
        return {
            "rejected": [
                {"id": finding.id, "status": finding.status},
                {"id": other.id, "status": other.status},
            ]
        }


# ReviewView.jsx의 splitDescription()과 순서를 맞춰야 한다 — 더 구체적인
# "제안" 태그부터 떼어야 "원본" 태그를 안 건드리고, "원본" 태그는 오른쪽
# 끝에 그대로 둔 채(재질문과 무관, original_text는 안 바뀜) 그 왼쪽의
# 제안 역번역 태그만 새 값으로 교체한다.
_PROPOSAL_BACK_TRANSLATION_RE = re.compile(r" \(제안 한국어 역번역 참고: .+\)$", re.DOTALL)
_ORIGINAL_BACK_TRANSLATION_RE = re.compile(r" \(원본 한국어 역번역 참고: .+\)$", re.DOTALL)
_PLAIN_BACK_TRANSLATION_RE = re.compile(r" \(한국어 역번역 참고: .+\)$", re.DOTALL)


def _refresh_proposal_back_translation(description: str, back_translation: str | None) -> str:
    without_proposal = _PROPOSAL_BACK_TRANSLATION_RE.sub("", description)
    original_match = _ORIGINAL_BACK_TRANSLATION_RE.search(without_proposal)
    original_suffix = original_match.group(0) if original_match else ""
    base = without_proposal[:original_match.start()] if original_match else without_proposal
    base = _PLAIN_BACK_TRANSLATION_RE.sub("", base)
    proposal_suffix = f" (한국어 역번역 참고: {back_translation})" if back_translation else ""
    return base + proposal_suffix + original_suffix


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
        _, _, _, glossary_entries = await _load_glossary_context(session, finding)

        try:
            new_suggested_text, back_translation = await requery_finding(
                finding, segment, payload.instruction, provider, knowledge, profile,
                glossary_entries=glossary_entries)
        except (RequeryNotSupportedError, RequeryNoResultError) as exc:
            raise HTTPException(400, str(exc))

        # 재질문도 S2와 같은 문제를 안고 있다 — AI가 문장을 다시 쓰면서
        # 이미 확정된 성별을 건드릴 수 있다. correct_stt와 동일하게 재적용한다.
        new_suggested_text = await apply_resolved_gender_to_text(
            segment, new_suggested_text, provider, profile)
        finding.suggested_text = new_suggested_text
        finding.status = "pending"
        finding.final_text = ""
        finding.reviewer_name = ""
        finding.reviewed_at = None
        # 역번역(검수자가 보는 "번역:" 태그)도 새 suggested_text 기준으로
        # 갱신한다 — 안 그러면 제안문은 바뀌었는데 역번역만 예전 그대로
        # 남는다(diagnosis). original_text 기준 "원본 한국어 역번역 참고"
        # 태그는 재질문과 무관하니 그대로 보존한다.
        refreshed_description = _refresh_proposal_back_translation(
            finding.description, back_translation)
        finding.description = f"[다시 질문: {payload.instruction}] {refreshed_description}"
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

        # 이 세그먼트에 finding이 이미 정확히 하나 있으면, 재검증 기준을
        # segment.target_text(원본 그대로)가 아니라 그 finding의 제안문으로
        # 바꾼다 — 안 그러면 "이미 다른 이유로 수정 제안이 나와 있는 줄"을
        # GPT가 항상 원본만 보고 판단해서, 기존 제안이 새 원문 기준으로도
        # 맞는지는 한 번도 확인 안 하고 넘어간다(회귀: 사용자 재현 — STT
        # 고쳐도 기존 제안 카드가 그대로였음).
        existing = await get_findings_for_segment(session, segment_id)
        current_text = existing[0].suggested_text if len(existing) == 1 else None
        episode = await session.get(Episode, tv.episode_id)
        glossary_entries = await get_glossary_prompt_entries(
            session, episode.title_id, tv.target_language, tv.variant)
        correction = await reverify_segment_after_stt_correction(
            seg, provider, knowledge, profile, current_text=current_text,
            glossary_entries=glossary_entries)

        if correction:
            # GPT가 새로 만든 제안문구는 1차 검수 때 이미 확정된 성별을
            # 전혀 모른다 — 여기서 재적용하지 않으면 "cansado/a"처럼 성별
            # 미확정 표기가 그대로 새어나간다. 재적용 후에도 여전히 성별
            # 확인이 필요하면(1차 검수 때는 없던 성별 표시 단어가 새로 생긴
            # 경우) 세그먼트를 다시 미확인 상태로 표시해 리뷰 화면에서
            # 사람에게 물어보게 한다.
            fixed_text = await apply_resolved_gender_to_text(
                seg, correction["corrected_text"], provider, profile)
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
        checked_text = current_text if current_text is not None else seg.target_text
        if correction and correction["corrected_text"] != checked_text:
            # 검수자가 스페인어를 몰라도 "원본"(수정 전 문장)이 무슨 뜻인지
            # 알아야 "제안"과 비교 판단이 가능하다 — 원본 문장 자체를 기계적으로
            # 역번역해 붙인다. 실패해도(네트워크 등) STT 재검증 자체는 막지
            # 않는다 — 역번역은 참고용 보조 정보다.
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
            if original_backtranslation:
                description += f" (원본 한국어 역번역 참고: {original_backtranslation})"

            # 제안의 역번역도 생성 (검수자가 제안을 판단할 때 참고하도록)
            proposal_backtranslation = None
            try:
                backtranslated = await provider.back_translate_with_claude(
                    [{"id": segment_id, "text": correction["corrected_text"]}], profile)
                if backtranslated:
                    proposal_backtranslation = backtranslated[0]["korean_text"]
            except Exception:
                logger.exception(
                    "제안 역번역 실패, 역번역 없이 계속 진행 (segment_id=%s)", segment_id)
            if proposal_backtranslation:
                description += f" (제안 한국어 역번역 참고: {proposal_backtranslation})"

            # 이 세그먼트에 finding이 이미 있으면(승인/거부 여부와 무관) 새 카드를
            # 또 만들지 않고 첫 번째 카드를 그 자리에서 갱신한다 — 새로 만들면
            # 같은 세그먼트에 대해 "승인됨" finding이 두 개 남을 수 있는데,
            # export가 최종 텍스트를 고를 때 reviewed_at 유무만 boolean으로
            # 비교해서(실제 시각 순서 비교 아님) 어느 쪽이 이길지 보장이 안 된다.
            # 의견 갈림 등으로 2개 이상이면 나머지는 옛 원문 기준의 낡은
            # 제안이므로 지운다. finding이 0개일 때만 새로 만든다.
            if existing:
                for stale in existing[1:]:
                    await session.delete(stale)
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


@router.post("/segments/{segment_id}/undo-split")
async def undo_split(segment_id: str):
    """분할로 생긴 큐(segment_id)를 지우고 원래 큐를 복원한다. 분할 후 고친 내용은 사라진다."""
    async with async_session() as session:
        new_seg = await session.get(Segment, segment_id)
        if new_seg is None:
            raise HTTPException(404, "segment not found")
        try:
            orig = await undo_split_in_session(session, new_seg)
        except SplitUndoError as e:
            raise HTTPException(409, str(e))
        await session.commit()
        return {"id": orig.id, "start": orig.start, "end": orig.end,
                "korean_text": orig.korean_text, "target_text": orig.target_text}


@router.post("/segments/{segment_id}/edit-target-text")
async def edit_target_text(segment_id: str, payload: EditTargetTextIn):
    """Finding이 안 걸린 줄도 번역문을 직접 고칠 수 있게 한다. export의
    _final_text_by_segment는 그 세그먼트에 반영된(approved/modified) finding이
    없으면 segment.target_text를 그대로 최종 텍스트로 쓰므로, 이 값만
    바꾸면 별도 finding 없이 바로 최종 SRT에 반영된다. 이력은 남기지 않는다
    (correct-stt의 SttCorrection과 달리 재검증도 하지 않는 단순 텍스트
    교체라 이력을 남길 이유가 약함)."""
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        findings = await get_findings_for_segment(session, segment_id)
        if any(f.status == "pending" for f in findings):
            raise HTTPException(409, "처리되지 않은 Finding이 있어 전체 자막에서 수정할 수 없습니다.")

        seg.target_text = payload.target_text
        now = datetime.now(timezone.utc)
        for finding in findings:
            if finding.status in ("approved", "modified"):
                finding.status = "modified"
                finding.final_text = payload.target_text
                finding.reviewer_name = payload.reviewer_name
                finding.reviewed_at = now
        await session.commit()
        return {"id": seg.id, "target_text": seg.target_text}


@router.post("/segments/{segment_id}/edit-timing")
async def edit_segment_timing(segment_id: str, payload: EditSegmentTimingIn):
    """자동/AI 정렬이 틀어진 큐를 검수자가 직접 시간으로 고칠 수 있게 한다.
    export(assemble_final_srt)는 seg.start로 정렬해 그대로 내보내므로, 이 값만
    바꾸면 별도 처리 없이 최종 SRT 순서·타임코드에 반영된다."""
    if payload.end <= payload.start:
        raise HTTPException(400, "종료 시간은 시작 시간보다 커야 합니다.")
    if payload.start < 0:
        raise HTTPException(400, "시작 시간은 0 이상이어야 합니다.")
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        seg.start = payload.start
        seg.end = payload.end
        await session.commit()
        return {"id": seg.id, "start": seg.start, "end": seg.end}


@router.post("/segments/{segment_id}/split")
async def split_segment(segment_id: str, payload: SplitSegmentIn):
    """한 큐에 여러 화자 대사가 섞여 길게 뭉쳐 있을 때, 검수자가 둘로 쪼갤 수 있게 한다.
    문구는 문장 경계(문장 끝 > 대사 전환 > 쉼표 > 접속사 …, format_rules._break_cost)에서
    둘로 나눠 담고 시간은 글자수 비율로 나눈다(큐당 최소 1초, split_cue). 한 조각에 대사
    대시가 하나뿐이면 대시는 뗀다. 나눌 수 없으면(경계 없음·시간이 2초 미만) 예전처럼 시간을
    반으로 나누고 문구는 양쪽에 복사해, 각 줄은 검수자가 기존 편집 UI로 다듬는다.
    나눈 뒤 되돌리기는 POST /segments/{새 큐 id}/undo-split."""
    async with async_session() as session:
        seg = await session.get(Segment, segment_id)
        if seg is None:
            raise HTTPException(404, "segment not found")
        new_seg = await split_segment_in_session(
            session, seg, payload.target_text or seg.target_text,
            await _line_break_rules(session, seg.target_version_id), fallback_copy=True)
        await session.commit()
        return {
            "original": {"id": seg.id, "start": seg.start, "end": seg.end,
                         "target_text": seg.target_text},
            "new": {
                "id": new_seg.id,
                "start": new_seg.start,
                "end": new_seg.end,
                "korean_text": new_seg.korean_text,
                "target_text": new_seg.target_text,
                "can_undo_split": True,
            },
        }
