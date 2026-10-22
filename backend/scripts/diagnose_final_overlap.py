"""읽기 전용: 최종 SRT와 같은 규칙(export._final_text_by_segment)으로 텍스트를
재구성해, 이웃 큐와의 내용 겹침이 (a) 원본 번역에 이미 있었는지 (b) 교정 반영
후 새로 생겼는지 센다. DB 수정/API 호출 없음.
실행: cd backend && venv/bin/python -m scripts.diagnose_final_overlap"""
import asyncio
import re
from difflib import SequenceMatcher

from sqlalchemy import select, text

from app.core.export import _final_text_by_segment
from app.db import async_session
from app.models import FindingRow, Segment, TargetVersion

MIN_CHARS = 15      # 겹친 연속 글자 수 하한
MIN_RATIO = 0.5     # 짧은 쪽 대비 겹침 비율 하한
WINDOW = 2          # 뒤따르는 몇 개 큐까지 비교할지
_norm = lambda s: re.sub(r"[\W_]+", " ", re.sub(r"</?[a-z][^>]*>", "", s.lower())).strip()


def overlap(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if len(a) < MIN_CHARS or len(b) < MIN_CHARS:
        return 0.0
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    r = m.size / min(len(a), len(b))
    return r if m.size >= MIN_CHARS and r >= MIN_RATIO else 0.0


async def main():
    async with async_session() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        versions = (await s.execute(select(TargetVersion).where(TargetVersion.deleted_at.is_(None)))).scalars().all()
        tot = pairs = pre = new = split_made = 0
        by_version, examples = [], []
        for v in versions:
            segs = (await s.execute(select(Segment).where(Segment.target_version_id == v.id))).scalars().all()
            fnd = (await s.execute(select(FindingRow).where(FindingRow.target_version_id == v.id))).scalars().all()
            final = _final_text_by_segment([
                {"segment_id": f.segment_id, "status": f.status, "final_text": f.final_text, "reviewed_at": f.reviewed_at}
                for f in fnd])
            segs = sorted([g for g in segs if not g.excluded and g.target_text.strip()], key=lambda g: g.start)
            tot += len(segs)
            vn = vp = vnew = 0
            for i, a in enumerate(segs):
                for b in segs[i + 1:i + 1 + WINDOW]:
                    pairs += 1
                    fa, fb = final.get(a.id, a.target_text), final.get(b.id, b.target_text)
                    if overlap(fa, fb) == 0:
                        continue
                    if a.korean_text and a.korean_text == b.korean_text:
                        split_made += 1          # 분할 기능이 복제한 쌍
                        continue
                    if overlap(a.target_text, b.target_text) > 0:
                        pre += 1; vp += 1        # 원본 번역에 이미 있던 겹침
                    else:
                        new += 1; vnew += 1      # 교정 반영 후 새로 생긴 겹침
                        if len(examples) < 6:
                            examples.append((v.id[:8], a.index, a.target_text[:70], fa[:70], fb[:70]))
            by_version.append((vp + vnew, vp, vnew, len(segs), v.id[:8]))
        print(f"언어판 {len(versions)}개, 큐 {tot}개, 비교 쌍(뒤 {WINDOW}개 창) {pairs}")
        print(f"겹침 총 {pre + new}쌍: 원본에 이미 있음 {pre} / 교정 반영 후 새로 생김 {new} / 분할 복제 {split_made}")
        print("겹침 많은 언어판 상위 5 (총, 원본, 교정후신규, 큐수, id):")
        for r in sorted(by_version, reverse=True)[:5]:
            print("  ", r)
        zero = sum(1 for r in by_version if r[0] == 0)
        print(f"겹침 0건인 언어판: {zero}/{len(versions)}")
        print("교정 후 새로 생긴 예시 (id, idx, 원본A / 최종A / 최종B):")
        for e in examples:
            print("  ", e)
        await s.rollback()

asyncio.run(main())
