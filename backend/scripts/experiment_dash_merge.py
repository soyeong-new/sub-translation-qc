"""실험(본 코드 수정 없음): 번역 쪽 대시 병합 금지를 풀면 정렬이 어떻게 달라지는지 비교한다.

한 편의 한국어 SRT와 번역 SRT를 pipeline과 같은 전처리로 읽어
 A) 현재 align_by_embedding_dp
 B) 번역 쪽(한글이 없는 텍스트) 대시 병합 금지를 끈 같은 함수
를 돌리고 서로 다른 묶음을 출력한다. 임베딩은 한 번만 호출해 캐시한다(OpenAI 임베딩 API 호출 발생).

실행: cd backend && venv/bin/python -m scripts.experiment_dash_merge <한국어.srt> <번역.srt>
"""

import asyncio
import json
import os
import re
import sys

import app.db  # noqa: F401 — import 시점에 backend/.env를 로드한다
import app.core.embedding_dp_alignment as dp
from app.core.alignment import detect_global_offset
from app.core.embedding_dp_alignment import _clean_text_for_embedding, align_by_embedding_dp
from app.core.ingest import load_srt
from app.providers.gpt_client import GptClient
from app.schemas import SegmentText

_HANGUL = re.compile(r"[가-힣]")


class CachingEmbeddings:
    """get_embeddings만 쓰는 정렬 함수에 넘기는 캐시 래퍼 — 같은 텍스트는 API를 한 번만 부른다."""

    def __init__(self, inner, path=None):
        self._inner, self._path, self.api_texts = inner, path, 0
        self._cache = json.load(open(path, encoding="utf-8")) if path and os.path.exists(path) else {}

    async def get_embeddings(self, texts):
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            self.api_texts += len(missing)
            for t, v in zip(missing, await self._inner.get_embeddings(missing)):
                self._cache[t] = v
            if self._path:
                json.dump(self._cache, open(self._path, "w", encoding="utf-8"))
        return [self._cache[t] for t in texts]


def summarize(label, pairs):
    both = sum(1 for p in pairs if p.korean and p.target)
    print(f"{label}: 묶음 {len(pairs)}개 (한국어+번역 {both}, 한국어만 {sum(1 for p in pairs if not p.target)},"
          f" 번역만 {sum(1 for p in pairs if not p.korean)})")


def key(p):
    return (p.korean.text if p.korean else None, p.target.text if p.target else None, round(p.target.start, 2) if p.target else None)


async def main(ko_path, es_path, cache_path=None, around=()):
    # 임베딩만 쓰므로 OpenAI 클라이언트만 만든다(Anthropic 키 불필요).
    provider = CachingEmbeddings(GptClient(api_key=os.getenv("OPENAI_API_KEY"), model="unused"), cache_path)
    raw_cues = load_srt(ko_path)
    kept = [(c, t) for c in raw_cues if (t := _clean_text_for_embedding(c.text))]
    korean = [SegmentText(start=c.start, end=c.end, text=t) for c, t in kept]
    kept_raw = [c for c, _ in kept]  # pipeline과 같이 korean과 1:1로 맞춘 원본
    target = load_srt(es_path)
    offset = detect_global_offset(korean, target)
    if offset:
        korean = [SegmentText(start=c.start + offset, end=c.end + offset, text=c.text) for c in korean]
    print(f"한국어 큐 {len(raw_cues)}(정제 후 {len(korean)}), 번역 큐 {len(target)}, 오프셋 {offset:+.2f}s")

    a = await align_by_embedding_dp(korean, target, provider, korean_raw_cues=kept_raw)

    original = dp._has_speaker_dash
    dp._has_speaker_dash = lambda text: False if not _HANGUL.search(text) else original(text)
    try:
        b = await align_by_embedding_dp(korean, target, provider, korean_raw_cues=kept_raw)
    finally:
        dp._has_speaker_dash = original

    print(f"임베딩 API로 보낸 고유 텍스트 {provider.api_texts}개")
    summarize("A 현재      ", a)
    summarize("B 번역 대시 해제", b)

    for sec in around:
        for label, pairs in (("A", a), ("B", b)):
            print(f"\n-- {label} @ {sec}s 부근 --")
            for p in pairs:
                t = (p.target or p.korean).start
                if abs(t - sec) <= 6:
                    print(f"[{t:.1f}] KO={p.korean.text if p.korean else None!r} | ES={p.target.text if p.target else None!r}")

    ka, kb = {key(p) for p in a}, {key(p) for p in b}
    only_a = [p for p in a if key(p) not in kb]
    only_b = [p for p in b if key(p) not in ka]
    print(f"\n서로 다른 묶음: A에만 {len(only_a)}, B에만 {len(only_b)}")
    for label, group in (("A에만(현재)", only_a), ("B에만(해제)", only_b)):
        print(f"\n== {label} ==")
        for p in group[:40]:
            t = f"{p.target.start:.1f}" if p.target else (f"{p.korean.start:.1f}" if p.korean else "?")
            print(f"[{t}] KO={p.korean.text if p.korean else None!r}\n      ES={p.target.text if p.target else None!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None, [float(x) for x in sys.argv[4:]]))
