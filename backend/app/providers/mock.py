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

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        findings = []
        for pair in pairs:
            if "BAD_TRANSLATION" in pair.get("target_text", ""):
                findings.append({
                    "segment_id": pair["id"], "category": "translation",
                    "description": "테스트용 오역 마커 감지",
                    "suggested_text": "texto corregido",
                    "confidence": 0.9,
                })
        return findings

    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        return [
            {"segment_id": hit["segment_id"], "description": "민감어 문맥 확인 필요",
             "severity": "medium"}
            for hit in term_hits
        ]
