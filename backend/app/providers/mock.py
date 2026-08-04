"""테스트 전용 결정론적 가짜 ModelProvider 구현체."""

from typing import List
from app.providers.base import ModelProvider


class MockProvider(ModelProvider):
    """결정론적 테스트 더블. 운영 경로에서는 base.get_provider()가 선택을 차단한다."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        checks = profile.get("checks_enabled", {})
        result = {"characters": [], "relationships": []}
        if checks.get("gender_agreement") or checks.get("register_consistency"):
            result["characters"] = [{"label": "인물1", "gendered_segment_ids": []}]
        return result

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               characters: List[dict], relationships: List[dict],
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        return [
            {"segment_id": hit["segment_id"], "category": "sensitivity",
             "corrected_text": "[교정됨]", "description": "테스트용 비속어 교정"}
            for hit in pending_sensitive_hits
        ]

    async def verify_and_refine(self, pairs: List[dict], original_target_by_id: dict,
                                 profile: dict, knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        return [
            {"segment_id": p["id"], "category": "translation",
             "corrected_text": "texto corregido", "description": "테스트용 오역 마커 감지"}
            for p in pairs if "BAD_TRANSLATION" in p.get("current_text", "")
        ]

    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        return text[:max_chars]
