"""Claude/GPT 클라이언트를 ModelProvider 인터페이스로 묶는 실제 프로바이더."""

from typing import List
from app.providers.base import ModelProvider
from app.providers.claude_client import ClaudeClient
from app.providers.gpt_client import GptClient


class LiveModelProvider(ModelProvider):
    def __init__(self, claude_api_key: str, claude_model: str,
                 gpt_api_key: str, gpt_model: str,
                 gpt_transcribe_model: str = "gpt-4o-mini-transcribe"):
        self._claude = ClaudeClient(api_key=claude_api_key, model=claude_model)
        self._gpt = GptClient(api_key=gpt_api_key, model=gpt_model,
                               transcribe_model=gpt_transcribe_model)

    async def transcribe(self, audio_path: str) -> List[dict]:
        return await self._gpt.transcribe(audio_path)

    async def analyze_characters(self, pairs: List[dict], profile: dict) -> dict:
        return await self._gpt.analyze_characters(pairs, profile)

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               characters: List[dict], relationships: List[dict],
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        return await self._claude.correct_primary(
            pairs, profile, characters, relationships, pending_sensitive_hits,
            knowledge, format_constraint, extra_instruction,
        )

    async def verify_and_refine(self, pairs: List[dict], original_target_by_id: dict,
                                 profile: dict, knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        return await self._gpt.verify_and_refine(
            pairs, original_target_by_id, profile, knowledge, format_constraint,
            extra_instruction,
        )

    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        return await self._claude.shrink_line(text, max_chars, max_lines, extra_instruction)
