"""테스트 전용 결정론적 가짜 ModelProvider 구현체."""

from typing import List
from app.providers.base import ModelProvider


class MockProvider(ModelProvider):
    """결정론적 테스트 더블. 운영 경로에서는 base.get_provider()가 선택을 차단한다."""

    async def transcribe(self, audio_path: str) -> List[dict]:
        return [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]

    async def correct_primary(self, pairs: List[dict], profile: dict,
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

    async def check_grammar_necessity(self, pairs: List[dict], profile: dict) -> List[dict]:
        """결정론적 테스트 규칙: target_text에 "cansad"(성별 표시 형용사
        예시 어간)가 있으면 gender_check_needed, "?"가 있으면
        formality_check_needed로 표시한다. 실제 문법 판단이 아니라 테스트용
        고정 규칙이다."""
        return [
            {"id": p["id"],
             "gender_check_needed": "cansad" in p.get("target_text", ""),
             "formality_check_needed": "?" in p.get("target_text", "")}
            for p in pairs
        ]
