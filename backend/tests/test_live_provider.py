from unittest.mock import AsyncMock
import pytest
from app.providers.live import LiveModelProvider


def _make_provider() -> LiveModelProvider:
    provider = LiveModelProvider(
        gemini_api_key="g", gemini_model="gm",
        claude_api_key="c", claude_model="cm",
        gpt_api_key="o", gpt_model="om",
    )
    provider._gemini.transcribe = AsyncMock(return_value=[{"start": 0.0, "end": 1.0, "text": "안녕"}])
    provider._gemini.analyze_characters = AsyncMock(
        return_value={"characters": [], "relationships": []}
    )
    provider._claude.review_translation = AsyncMock(
        return_value=[{"segment_id": "p1", "category": "translation",
                        "description": "클로드", "suggested_text": "a", "confidence": 0.8}]
    )
    provider._gpt.review_translation = AsyncMock(
        return_value=[{"segment_id": "p1", "category": "translation",
                        "description": "GPT", "suggested_text": "b", "confidence": 0.7}]
    )
    provider._claude.check_sensitivity = AsyncMock(return_value=[])
    provider._gpt.check_sensitivity = AsyncMock(return_value=[])
    return provider


@pytest.mark.asyncio
async def test_transcribe_delegates_to_gemini():
    provider = _make_provider()
    result = await provider.transcribe("/fake/audio.wav")
    assert result == [{"start": 0.0, "end": 1.0, "text": "안녕"}]


@pytest.mark.asyncio
async def test_analyze_characters_delegates_to_gemini():
    provider = _make_provider()
    result = await provider.analyze_characters([], {})
    assert result == {"characters": [], "relationships": []}


@pytest.mark.asyncio
async def test_review_translation_ensembles_claude_and_gpt():
    provider = _make_provider()
    result = await provider.review_translation([], "", {}, "")
    assert len(result) == 2
    models = {r["model"] for r in result}
    assert models == {"claude", "gpt"}


@pytest.mark.asyncio
async def test_check_sensitivity_ensembles_claude_and_gpt():
    provider = _make_provider()
    result = await provider.check_sensitivity([], [])
    assert result == []
