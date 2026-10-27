"""읽기 전용 시험: 이미 분석된 회차에 성별 어긋남 검사(_run_gender_mismatch_pass)를 돌려
새 카드 / 기존 카드 제안문 수정이 어떻게 나오는지 출력한다. DB는 수정하지 않는다.
어긋난 줄 수만큼 실제 LLM(GPT 수정문 + Claude 역번역)을 호출한다 — 호출 전에 대상 수를
출력하고, MAX_ITEMS를 넘으면 --yes 없이는 멈춘다.
실행: cd backend && venv/bin/python -m scripts.check_gender_mismatch <target_version_id> [--yes]"""
import asyncio
import sys

from sqlalchemy import select, text

import app.db  # noqa: F401 — .env 로드(앱과 같은 방식)
from app.core.gender_check import find_gender_mismatches
from app.core.grammar_necessity import _resolve_model
from app.core.pipeline import _build_resolved_registers, _run_gender_mismatch_pass
from app.db import async_session
from app.language_profiles.loader import load_profile
from app.models import FindingRow, Segment, TargetVersion
from app.providers.base import get_provider
from app.schemas import AlignedPair, Finding, SegmentText

MAX_ITEMS = 15


def _mmss(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


async def main(tv_id: str, yes: bool):
    async with async_session() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        tv = await s.get(TargetVersion, tv_id)
        segs = (await s.execute(select(Segment).where(Segment.target_version_id == tv_id)
                                .order_by(Segment.index))).scalars().all()
        rows = (await s.execute(select(FindingRow).where(
            FindingRow.target_version_id == tv_id, FindingRow.category != "formatting"))).scalars().all()
    profile = load_profile(tv.target_language, tv.variant)

    pairs = [AlignedPair(
        id=g.id, korean=SegmentText(start=g.start, end=g.end, text=g.korean_text) if g.korean_text else None,
        target=SegmentText(start=g.start, end=g.end, text=g.target_text)) for g in segs if g.target_text]
    start_by_id = {g.id: g.start for g in segs}
    registers = _build_resolved_registers([
        {"segment_id": g.id, "resolved_gender_groups": g.resolved_gender_groups_raw,
         "resolved_gender": g.resolved_gender_raw, "resolved_formality": g.resolved_formality_raw}
        for g in segs])
    existing = [Finding(
        id=r.id, target_version_id=tv_id, segment_id=r.segment_id, category=r.category,
        description=r.description, original_text=r.original_text, suggested_text=r.suggested_text,
        confidence=r.confidence, source=r.source, model=r.model, status=r.status, final_text=r.final_text)
        for r in rows]
    before = {f.id: (f.suggested_text, f.description) for f in existing}

    # 호출 전 대상 수(무료 — spaCy만 사용)
    nlp = _resolve_model(profile["language"])
    new_cards = find_gender_mismatches(pairs, registers, nlp, skip_ids={f.segment_id for f in existing})
    cards = {f.id: f for f in existing if f.segment_id in registers}
    card_fixes = find_gender_mismatches(
        [AlignedPair(id=fid, target=SegmentText(start=0, end=1, text=f.suggested_text)) for fid, f in cards.items()],
        {fid: registers[f.segment_id] for fid, f in cards.items()}, nlp)
    print(f"성별 확정 줄 {sum(1 for r in registers.values() if r.get('gender_groups'))}개, "
          f"기존 finding {len(existing)}건")
    print(f"→ 새 카드 후보 {len(new_cards)}줄, 기존 카드 제안문 수정 후보 {len(card_fixes)}건")
    for pid, groups in new_cards.items():
        print(f"   새 카드 {_mmss(start_by_id[pid])} {[g['words'] for g in groups]}")
    for fid, groups in card_fixes.items():
        print(f"   카드 수정 {_mmss(start_by_id[cards[fid].segment_id])} {[g['words'] for g in groups]}")
    total = len(new_cards) + len(card_fixes)
    if total == 0:
        return
    if total > MAX_ITEMS and not yes:
        print(f"\n{total}건이라 LLM 호출 비용이 커질 수 있어 멈춥니다. 진행하려면 --yes를 붙이세요.")
        return

    warnings: list = []
    created = await _run_gender_mismatch_pass(
        pairs, get_provider(), profile, tv_id, registers, existing, warnings)
    print(f"\n== 새 카드 {len(created)}건 ==")
    for f in created:
        print(f"[{_mmss(start_by_id[f.segment_id])}] {f.original_text!r}\n   → {f.suggested_text!r}\n   {f.description}")
    changed = [f for f in existing if (f.suggested_text, f.description) != before[f.id]]
    print(f"\n== 기존 카드 제안문 수정 {len(changed)}건 ==")
    for f in changed:
        print(f"[{_mmss(start_by_id[f.segment_id])}] {before[f.id][0]!r}\n   → {f.suggested_text!r}\n   {f.description}")
    print("\nwarnings:", warnings)
    print(f"수정문 검사를 통과 못해 버려진 줄: {total - len(created) - len(changed)}건")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--yes" in sys.argv))
