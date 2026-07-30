"""Gemini/Claude/GPT 클라이언트를 ModelProvider 인터페이스로 묶는 실제 프로바이더."""

from typing import List
from app.providers.base import ModelProvider
from app.providers.gemini_client import GeminiClient
from app.providers.claude_client import ClaudeClient
from app.providers.gpt_client import GptClient
from app.providers.ensemble import call_both


class LiveModelProvider(ModelProvider):
    def __init__(self, gemini_api_key: str, gemini_model: str,
                 claude_api_key: str, claude_model: str,
                 gpt_api_key: str, gpt_model: str):
        self._gemini = GeminiClient(api_key=gemini_api_key, model=gemini_model)
        self._claude = ClaudeClient(api_key=claude_api_key, model=claude_model)
        self._gpt = GptClient(api_key=gpt_api_key, model=gpt_model)

    async def transcribe(self, audio_path: str) -> List[dict]:
        return await self._gemini.transcribe(audio_path)

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        return await self._gemini.analyze_characters(pairs, profile)

    async def review_translation(self, pairs: List[dict], knowledge: str,
                                  profile: dict, format_constraint: str) -> List[dict]:
        return await call_both(
            "claude", self._claude.review_translation(pairs, knowledge, profile, format_constraint),
            "gpt", self._gpt.review_translation(pairs, knowledge, profile, format_constraint),
        )

    async def check_sensitivity(self, pairs: List[dict], term_hits: List[dict]) -> List[dict]:
        return await call_both(
            "claude", self._claude.check_sensitivity(pairs, term_hits),
            "gpt", self._gpt.check_sensitivity(pairs, term_hits),
        )
