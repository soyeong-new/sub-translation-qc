"""테스트 전용 결정론적 가짜 ModelProvider 구현체."""

from typing import List
from app.providers.base import ModelProvider


def _detect_corrections(pairs: List[dict], pending_sensitive_hits: List[dict]) -> List[dict]:
    """correct_primary/verify_and_refine이 공유하는 결정론적 테스트 규칙.
    기본 동작은 둘 다 똑같은 규칙을 쓰게 해(대칭 시그니처이므로 가능),
    합의(둘 다 같은 줄을 지적)가 기본값이 되게 한다 — 불일치 경로를 테스트하려면
    개별 테스트가 둘 중 하나를 monkeypatch해서 다르게 만든다."""
    corrections = [
        {"segment_id": p["id"], "category": "mistranslation",
         "corrected_text": "texto corregido", "description": "테스트용 오역 마커 감지"}
        for p in pairs if "BAD_TRANSLATION" in p.get("target_text", "")
    ]
    corrections += [
        {"segment_id": hit["segment_id"], "category": "sensitivity",
         "corrected_text": "[교정됨]", "description": "테스트용 비속어 교정"}
        for hit in pending_sensitive_hits
    ]
    return corrections


def _check_equivalence(items: List[dict]) -> List[dict]:
    """check_equivalence_with_claude/_with_gpt가 공유하는 결정론적 테스트 규칙."""
    return [{"id": i["id"], "equivalent": i["text_a"] == i["text_b"]} for i in items]


class MockProvider(ModelProvider):
    """결정론적 테스트 더블. 운영 경로에서는 base.get_provider()가 선택을 차단한다."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        return _detect_corrections(pairs, pending_sensitive_hits)

    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        return _detect_corrections(pairs, pending_sensitive_hits)

    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        return text[:max_chars]

    async def back_translate_with_claude(self, texts: List[dict], profile: dict) -> List[dict]:
        return [{"id": t["id"], "korean_text": f"[역번역:{t['text']}]"} for t in texts]

    async def back_translate_with_gpt(self, texts: List[dict], profile: dict) -> List[dict]:
        return [{"id": t["id"], "korean_text": f"[역번역:{t['text']}]"} for t in texts]

    async def check_equivalence_with_claude(self, items: List[dict], profile: dict) -> List[dict]:
        return _check_equivalence(items)

    async def check_equivalence_with_gpt(self, items: List[dict], profile: dict) -> List[dict]:
        return _check_equivalence(items)
