"""검수 완료된 내용을 최종 SRT로 조립해 내보내는 엔드포인트."""

from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from app.db import async_session
from app.models import TargetVersion, FindingRow, Segment, ExportRow, Episode, Title, GlossaryFix
from app.core.export import (
    assemble_final_srt, compute_stats, safety_net_check, build_export_filename,
    glossary_consistency_check, name_variant_groups, unconfirmed_amount_check, apply_replacements,
    resolved_text_by_segment, repeated_line_mismatches,
)
from app.language_profiles.loader import load_profile
from app.providers.base import get_provider
from app.repositories import get_glossary_prompt_entries, get_unconfirmed_amount_terms

router = APIRouter()


async def _load_export_context(session, target_version_id: str):
    """export 조립에 필요한 tv/episode/title/segments/findings를 한 번에 불러온다
    — 전체 검사(GET export)와 재조립만 하는 reassemble이 둘 다 필요로 한다."""
    tv = await session.get(TargetVersion, target_version_id)
    if tv is None:
        raise HTTPException(404, "target version not found")
    episode = await session.get(Episode, tv.episode_id)
    title = await session.get(Title, episode.title_id)
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
    segments = [
        {"id": s.id, "start": s.start, "end": s.end, "text": s.target_text,
         "korean_text": s.korean_text, "excluded": s.excluded,
         "target_cues": s.target_cues_raw}
        for s in seg_rows
    ]
    # reviewed_at도 함께 넘긴다: 같은 세그먼트에 자동보정과 검수자 판단이 동시에
    # 걸린 경우 어느 쪽이 최종 텍스트가 되는지 결정하는 데 쓰인다 (검수자 우선).
    findings = [{"segment_id": f.segment_id, "status": f.status,
                 "final_text": f.final_text, "reviewed_at": f.reviewed_at}
                for f in finding_rows]
    return tv, episode, title, segments, findings


async def _load_fixes(session, target_version_id: str) -> List[GlossaryFix]:
    return (await session.execute(
        select(GlossaryFix).where(GlossaryFix.target_version_id == target_version_id)
        .order_by(GlossaryFix.created_at)
    )).scalars().all()


def _with_applied_fixes(findings: list, fixes: List[GlossaryFix]) -> list:
    """적용한 수정(용어집·반복 대사)을 최종 텍스트 계산용 finding처럼 덧붙인다(통계에는 쓰지 않는다)."""
    return findings + [
        {"segment_id": f.segment_id, "status": "approved", "final_text": f.after_text,
         "reviewed_at": None, "_fix": True}
        for f in fixes
    ]


async def _check_warnings(session, tv, episode, segments, findings):
    """줄 길이 안전망 + 용어집 일치 검사. 첫 검사와 재조립이 같이 쓴다.
    용어집은 호출 시점에 새로 읽는다 — 검수 중 추가된 용어도 잡히도록."""
    glossary_entries = await get_glossary_prompt_entries(
        session, episode.title_id, tv.target_language, tv.variant)
    # 안전망 (design §5-1의 3번 지점): assemble_final_srt와 동일한 최종 텍스트를
    # 대상으로 줄 길이를 마지막으로 한 번 더 검사한다. 위반이 있어도 내보내기
    # 자체는 막지 않고 참고용 경고로만 응답에 포함한다 (non-blocking).
    warnings = safety_net_check(segments, findings)
    profile = load_profile(tv.target_language, tv.variant)
    warnings += await glossary_consistency_check(
        segments, findings, glossary_entries, get_provider(), profile)
    warnings += unconfirmed_amount_check(
        segments, await get_unconfirmed_amount_terms(
            session, episode.title_id, tv.target_language, tv.variant))
    return warnings


def _fix_dicts(fixes, kind):
    return [{"segment_id": f.segment_id, "before_text": f.before_text, "after_text": f.after_text}
            for f in fixes if f.kind == kind]


def _export_payload(srt, title, episode, tv, findings, warnings, fixes=(), repeated_groups=(), name_groups=()):
    return {
        # 같은 이름이 철자만 달리 쓰인 묶음 — 사용자가 쓸 표기를 고른다(표기 통일 탭).
        "name_variant_groups": list(name_groups),
        # 한국어 원문이 같거나 비슷한데 번역이 다른 줄 묶음 + 적용한 수정(되돌리기용).
        "repeated_groups": list(repeated_groups),
        "repeated_fixes": _fix_dicts(fixes, "repeated"),
        # 적용한 용어집 수정(되돌리기용) — 내보내기 창이 보여준다.
        "glossary_fixes": _fix_dicts(fixes, "applied"),
        "srt": srt,
        "stats": compute_stats(findings).model_dump(),
        "format_warnings": [w.model_dump() for w in warnings],
        "filename": build_export_filename(
            title.name, episode.episode_no, tv.target_language, tv.variant
        ),
    }


@router.get("/target-versions/{target_version_id}/applied-fixes")
async def applied_fixes(target_version_id: str):
    """적용한 용어집·반복 대사 수정 전체(적용 순서대로) — 검수 화면이 전체 자막에 바로 보여주려고 쓴다.
    내보내기 검사(LLM 포함)를 거치지 않는 가벼운 조회다."""
    async with async_session() as session:
        if await session.get(TargetVersion, target_version_id) is None:
            raise HTTPException(404, "target version not found")
        fixes = await _load_fixes(session, target_version_id)
    return [{"segment_id": f.segment_id, "kind": f.kind,
             "before_text": f.before_text, "after_text": f.after_text} for f in fixes]


@router.get("/target-versions/{target_version_id}/export")
async def export_target_version(target_version_id: str):
    async with async_session() as session:
        tv, episode, title, segments, findings = await _load_export_context(
            session, target_version_id)
        fixes = await _load_fixes(session, target_version_id)
        effective = _with_applied_fixes(findings, fixes)
        warnings = await _check_warnings(session, tv, episode, segments, effective)
        name_groups = name_variant_groups(segments, effective, await get_glossary_prompt_entries(
            session, episode.title_id, tv.target_language, tv.variant))
    return _export_payload(
        assemble_final_srt(segments, effective, load_profile(tv.target_language, tv.variant)),
        title, episode, tv, findings, warnings, fixes,
        repeated_line_mismatches(segments, effective), name_groups)


@router.get("/target-versions/{target_version_id}/export/reassemble")
async def reassemble_export(target_version_id: str):
    """검수자가 export 팝업을 띄워둔 채로 findings/segments/용어집을 더 고쳤을 때, "최신 내용으로
    반영"을 누르면 SRT를 다시 조립하고 전체 세그먼트에 대해 용어집/포맷 검사도 다시 돌린다
    (LLM 호출 포함)."""
    return await export_target_version(target_version_id)


class ReplacementIn(BaseModel):
    from_text: str
    to_text: str


class GlossaryFixItemIn(BaseModel):
    segment_id: str
    replacements: List[ReplacementIn]


class GlossaryFixesIn(BaseModel):
    items: List[GlossaryFixItemIn]


class GlossaryFixUndoIn(BaseModel):
    segment_id: str


class RepeatedFixItemIn(BaseModel):
    segment_id: str
    new_text: str


class RepeatedFixesIn(BaseModel):
    items: List[RepeatedFixItemIn]


@router.post("/target-versions/{target_version_id}/glossary-fixes")
async def apply_glossary_fixes(target_version_id: str, payload: GlossaryFixesIn):
    """경고 카드의 "적용"(여러 줄이면 "모두 적용") — 줄마다 틀린 표기를 등록 표기로 바꾼 문구를
    기록한다. 지우면(undo) 되돌려진다. 바꿀 표기가 줄에 없으면 그 줄은 건너뛴다(skipped)."""
    async with async_session() as session:
        tv, _, _, segments, findings = await _load_export_context(session, target_version_id)
        fixes = await _load_fixes(session, target_version_id)
        current = resolved_text_by_segment(segments, _with_applied_fixes(findings, fixes))
        applied_by_segment = {f.segment_id: f for f in fixes if f.kind == "applied"}
        applied, skipped = 0, []
        for item in payload.items:
            if item.segment_id not in current:
                skipped.append(item.segment_id)
                continue
            before = current[item.segment_id]
            after = apply_replacements(before, [(r.from_text, r.to_text) for r in item.replacements])
            if after == before:
                skipped.append(item.segment_id)
                continue
            existing = applied_by_segment.get(item.segment_id)
            if existing is None:
                session.add(GlossaryFix(
                    target_version_id=target_version_id, segment_id=item.segment_id,
                    kind="applied", before_text=before, after_text=after))
            else:  # 같은 줄에 또 적용하면 처음 문구는 그대로 두고 결과만 갱신
                existing.after_text = after
            applied += 1
        await session.commit()
    return {"applied": applied, "skipped": skipped}


async def _undo_fix(target_version_id: str, segment_id: str, kind: str):
    async with async_session() as session:
        await session.execute(delete(GlossaryFix).where(
            GlossaryFix.target_version_id == target_version_id,
            GlossaryFix.segment_id == segment_id, GlossaryFix.kind == kind))
        await session.commit()
    return {"ok": True}


@router.post("/target-versions/{target_version_id}/glossary-fixes/undo")
async def undo_glossary_fix(target_version_id: str, payload: GlossaryFixUndoIn):
    return await _undo_fix(target_version_id, payload.segment_id, "applied")


@router.post("/target-versions/{target_version_id}/repeated-fixes")
async def apply_repeated_fixes(target_version_id: str, payload: RepeatedFixesIn):
    """반복 대사 카드의 "적용" — 체크한 줄의 번역을 고른 대표 번역으로 통째로 바꾼다.
    지우면(undo) 되돌려진다. 이미 같은 문구인 줄은 건너뛴다(skipped)."""
    async with async_session() as session:
        _, _, _, segments, findings = await _load_export_context(session, target_version_id)
        fixes = await _load_fixes(session, target_version_id)
        current = resolved_text_by_segment(segments, _with_applied_fixes(findings, fixes))
        existing_by_segment = {f.segment_id: f for f in fixes if f.kind == "repeated"}
        applied, skipped = 0, []
        for item in payload.items:
            new_text = item.new_text.strip()
            before = current.get(item.segment_id)
            if before is None or not new_text or new_text == before:
                skipped.append(item.segment_id)
                continue
            existing = existing_by_segment.get(item.segment_id)
            if existing is None:
                session.add(GlossaryFix(
                    target_version_id=target_version_id, segment_id=item.segment_id,
                    kind="repeated", before_text=before, after_text=new_text))
            else:  # 같은 줄에 또 적용하면 처음 문구는 그대로 두고 결과만 갱신
                existing.after_text = new_text
            applied += 1
        await session.commit()
    return {"applied": applied, "skipped": skipped}


@router.post("/target-versions/{target_version_id}/repeated-fixes/undo")
async def undo_repeated_fix(target_version_id: str, payload: GlossaryFixUndoIn):
    return await _undo_fix(target_version_id, payload.segment_id, "repeated")


@router.post("/target-versions/{target_version_id}/export/confirm")
async def confirm_export(target_version_id: str):
    """검사(export GET)와 별개로, 검수자가 실제로 파일을 다운로드했을 때만
    호출한다 — export 이력/감사 기록(exports 테이블)은 검사 횟수가 아니라
    실제 내보낸 횟수를 남겨야 하므로, LLM까지 도는 검사 호출에 얹지 않고
    분리했다. 영상 프록시는 여기서 지우지 않는다 — export 후에도 계속
    검토/재수정하는 흐름이 흔해서, 한 번 내보냈다고 미리보기가 영구히
    사라지면 안 된다(원본 영상은 이미 삭제된 뒤라 복구 불가). 삭제는
    title을 실제로 지울 때만 한다(titles.py delete_title)."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        finding_rows = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == target_version_id)
        )).scalars().all()
        findings = [{"segment_id": f.segment_id, "status": f.status,
                     "final_text": f.final_text, "reviewed_at": f.reviewed_at}
                    for f in finding_rows]
        stats = compute_stats(findings)
        session.add(ExportRow(
            target_version_id=target_version_id,
            finding_count=stats.finding_count,
            reflection_rate=stats.reflection_rate,
        ))
        await session.commit()
    return {"ok": True}
