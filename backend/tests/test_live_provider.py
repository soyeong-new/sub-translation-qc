from unittest.mock import AsyncMock
import pytest
from app.providers.live import LiveModelProvider


def _make_provider() -> LiveModelProvider:
    provider = LiveModelProvider(
        claude_api_key="c", claude_model="cm",
        gpt_api_key="o", gpt_model="om",
    )
    provider._gpt.transcribe = AsyncMock(return_value=[{"start": 0.0, "end": 1.0, "text": "안녕"}])
    provider._gpt.analyze_characters = AsyncMock(
        return_value={"characters": [], "relationships": []}
    )
    provider._claude.correct_primary = AsyncMock(
        return_value=[{"segment_id": "p1", "category": "gender",
                        "corrected_text": "está feliz", "description": "클로드 교정"}]
    )
    provider._gpt.verify_and_refine = AsyncMock(
        return_value=[{"segment_id": "p1", "category": "translation",
                        "corrected_text": "está muy feliz", "description": "GPT 검증"}]
    )
    provider._claude.shrink_line = AsyncMock(return_value="짧아진 문장")
    return provider


@pytest.mark.asyncio
async def test_transcribe_delegates_to_gpt():
    provider = _make_provider()
    result = await provider.transcribe("/fake/audio.wav")
    assert result == [{"start": 0.0, "end": 1.0, "text": "안녕"}]


@pytest.mark.asyncio
async def test_analyze_characters_delegates_to_gpt():
    provider = _make_provider()
    result = await provider.analyze_characters([], {})
    assert result == {"characters": [], "relationships": []}


@pytest.mark.asyncio
async def test_correct_primary_delegates_to_claude():
    provider = _make_provider()
    result = await provider.correct_primary([], {}, [], [], [], "", "")
    assert result[0]["corrected_text"] == "está feliz"
    provider._claude.correct_primary.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_and_refine_delegates_to_gpt():
    provider = _make_provider()
    result = await provider.verify_and_refine([], {}, {}, "", "")
    assert result[0]["corrected_text"] == "está muy feliz"
    provider._gpt.verify_and_refine.assert_awaited_once()


@pytest.mark.asyncio
async def test_shrink_line_delegates_to_claude():
    provider = _make_provider()
    result = await provider.shrink_line("긴 문장", max_chars=50, max_lines=2)
    assert result == "짧아진 문장"
    provider._claude.shrink_line.assert_awaited_once()
