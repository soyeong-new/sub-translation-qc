"""읽기 전용 품질 진단: 정렬 중복 / 검수자 판정 통계 / STT 캐시 타이밍.

DB를 수정하지 않고(READ ONLY 트랜잭션) API도 호출하지 않는다.
실행: cd backend && venv/bin/python -m scripts.diagnose_quality
"""

import asyncio
import difflib
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select, text

from app.core.ingest import load_srt
from app.db import async_session
from app.models import Episode, FindingRow, Segment, TargetVersion

NGRAM = 4
DUP_THRESHOLD = 0.3


def _pct(n, d):
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0"


def _quantiles(xs):
    if not xs:
        return "n/a"
    xs = sorted(xs)
    return f"중앙 {statistics.median(xs):.2f} / p90 {xs[int(len(xs) * 0.9) - 1 if len(xs) > 1 else 0]:.2f} / 최대 {xs[-1]:.2f}"


def _ngrams(s: str):
    toks = s.lower().split()
    return {tuple(toks[i:i + NGRAM]) for i in range(len(toks) - NGRAM + 1)}


async def section_alignment(s):
    print("\n## 1. 정렬: 반쪽 짝 / 이웃 중복")
    versions = (await s.execute(select(TargetVersion).where(TargetVersion.deleted_at.is_(None)))).scalars().all()
    tot = half_k = half_t = excl = adjacent = dup = 0
    worst = []
    for v in versions:
        segs = (await s.execute(
            select(Segment).where(Segment.target_version_id == v.id).order_by(Segment.index)
        )).scalars().all()
        tot += len(segs)
        for sg in segs:
            if sg.korean_text and not sg.target_text:
                half_k += 1
            if sg.target_text and not sg.korean_text:
                half_t += 1
            excl += sg.excluded
        for a, b in zip(segs, segs[1:]):
            ga, gb = _ngrams(a.target_text), _ngrams(b.target_text)
            if not ga or not gb:
                continue
            adjacent += 1
            c = len(ga & gb) / min(len(ga), len(gb))
            if c >= DUP_THRESHOLD:
                dup += 1
                worst.append((c, v.id[:8], a.index, a.target_text[:60], b.target_text[:60]))
    print(f"- 언어판 {len(versions)}개, 세그먼트 {tot}개")
    print(f"- 한국어만 있음 {_pct(half_k, tot)}, 번역만 있음 {_pct(half_t, tot)}, 제외 표시 {_pct(excl, tot)}")
    print(f"- 이웃 번역 {NGRAM}-gram 중복(≥{DUP_THRESHOLD}): {_pct(dup, adjacent)}")
    for c, vid, idx, a, b in sorted(worst, reverse=True)[:5]:
        print(f"  · {c:.2f} [{vid} #{idx}] {a!r} | {b!r}")


async def section_findings(s):
    print("\n## 2. 검수자 판정 통계")
    rows = (await s.execute(select(FindingRow))).scalars().all()
    print(f"- 지적 {len(rows)}건, 상태 분포: {dict(Counter(r.status for r in rows))}")
    by_cat = defaultdict(Counter)
    for r in rows:
        by_cat[r.category][r.status] += 1
    print("- 카테고리별 상태:")
    for cat, c in sorted(by_cat.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  · {cat}: {dict(c)}")
    by_src = defaultdict(Counter)
    for r in rows:
        by_src[(r.source, r.model)][r.status] += 1
    print("- source/model별 상태:")
    for k, c in sorted(by_src.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  · {k}: {dict(c)}")
    # 검수자가 제안문을 그대로 쓴 비율 vs 고쳐 쓴 비율
    reviewed = [r for r in rows if r.reviewed_at and r.final_text and r.suggested_text]
    same = sum(r.final_text.strip() == r.suggested_text.strip() for r in reviewed)
    print(f"- 검수 완료 {len(reviewed)}건 중 제안문 그대로 {_pct(same, len(reviewed))}")
    sims = [difflib.SequenceMatcher(None, r.suggested_text, r.final_text).ratio()
            for r in reviewed if r.final_text.strip() != r.suggested_text.strip()]
    print(f"- 고쳐 쓴 건의 제안문↔최종 유사도: {_quantiles(sims)}")


async def section_stt(s):
    print("\n## 3. STT 캐시 타이밍")
    eps = (await s.execute(select(Episode).where(Episode.stt_cache.is_not(None)))).scalars().all()
    print(f"- STT 캐시가 있는 회차 {len(eps)}개")
    start_d, end_d, shown = [], [], False
    used = 0
    for ep in eps:
        words = (ep.stt_cache or {}).get("segments") or []
        if words and not shown:
            print(f"- 캐시 항목 키 예시: {sorted(words[0].keys()) if isinstance(words[0], dict) else type(words[0])}")
            shown = True
        if not ep.korean_srt_path or not Path(ep.korean_srt_path).exists():
            continue
        cues = load_srt(ep.korean_srt_path)
        by_cue = defaultdict(list)
        for w in words:
            if isinstance(w, dict) and w.get("korean_cue_index") is not None:
                by_cue[w["korean_cue_index"]].append(w)
        if not by_cue:
            continue
        used += 1
        for i, ws in by_cue.items():
            if 0 <= i < len(cues):
                start_d.append(min(w["start"] for w in ws) - cues[i].start)
                end_d.append(max(w["end"] for w in ws) - cues[i].end)
    print(f"- SRT 파일이 남아 있고 cue 인덱스가 있는 회차 {used}개")
    print(f"- 시작 시각 차(STT−SRT, 초) |값|: {_quantiles([abs(x) for x in start_d])}; 부호 중앙값 {statistics.median(start_d) if start_d else 'n/a'}")
    print(f"- 종료 시각 차(STT−SRT, 초) |값|: {_quantiles([abs(x) for x in end_d])}")
    print("- 주의: 캐시 텍스트는 SRT 텍스트로 교체된 값일 수 있어 CER은 여기서 계산하지 않는다.")


async def main():
    try:
        async with async_session() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            await section_alignment(s)
            await section_findings(s)
            await section_stt(s)
            await s.rollback()
    except OSError as e:
        print(f"DB에 연결하지 못했다: {e}")


if __name__ == "__main__":
    asyncio.run(main())
