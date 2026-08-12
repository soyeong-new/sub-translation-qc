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

    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "") -> List[dict]:
        return await self._claude.correct_primary(
            pairs, profile, pending_sensitive_hits,
            knowledge, format_constraint, extra_instruction,
        )

    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "") -> List[dict]:
        return await self._gpt.verify_and_refine(
            pairs, profile, pending_sensitive_hits,
            knowledge, format_constraint, extra_instruction,
        )

    async def shrink_line(self, text: str, max_chars: int, max_lines: int,
                           extra_instruction: str = "") -> str:
        return await self._claude.shrink_line(text, max_chars, max_lines, extra_instruction)

    async def back_translate_with_claude(self, texts: List[dict], profile: dict) -> List[dict]:
        return await self._claude.back_translate(texts, profile)

    async def back_translate_with_gpt(self, texts: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.back_translate(texts, profile)

    async def check_equivalence_with_claude(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._claude.check_equivalence(items, profile)

    async def check_equivalence_with_gpt(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.check_equivalence(items, profile)

    async def split_scenes(self, pairs: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.split_scenes(pairs, profile)

    async def gloss_words(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.gloss_words(items, profile)

    async def apply_formality(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.apply_formality(items, profile)

    async def resolve_gender_from_context(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.resolve_gender_from_context(items, profile)
