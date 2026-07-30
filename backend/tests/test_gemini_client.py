import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.gemini_client import GeminiClient


def _make_client_with_fake_sdk(response_text: str) -> GeminiClient:
    client = GeminiClient(api_key="fake", model="gemini-test")
    fake_response = MagicMock()
    fake_response.text = response_text
    client._sdk_client.aio.models.generate_content = AsyncMock(return_value=fake_response)
    client._sdk_client.files.upload = MagicMock(return_value=MagicMock(name="fake_file"))
    return client


@pytest.mark.asyncio
async def test_transcribe_parses_structured_json_response():
    payload = [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.transcribe("/fake/audio.wav")
    assert result == payload


@pytest.mark.asyncio
async def test_transcribe_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("이건 JSON이 아님")
    with pytest.raises(ValueError):
        await client.transcribe("/fake/audio.wav")


@pytest.mark.asyncio
async def test_transcribe_raises_on_none_response_text():
    client = _make_client_with_fake_sdk(None)
    with pytest.raises(ValueError):
        await client.transcribe("/fake/audio.wav")


@pytest.mark.asyncio
async def test_analyze_characters_parses_structured_json_response():
    payload = {
        "characters": [{"label": "민수", "gendered_segment_ids": ["p1"]}],
        "relationships": [],
    }
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.analyze_characters(
        [{"id": "p1", "target_text": "hola"}],
        {"checks_enabled": {"gender_agreement": True}},
    )
    assert result == payload


@pytest.mark.asyncio
async def test_analyze_characters_raises_on_none_response_text():
    client = _make_client_with_fake_sdk(None)
    with pytest.raises(ValueError):
        await client.analyze_characters(
            [{"id": "p1", "target_text": "hola"}],
            {"checks_enabled": {"gender_agreement": True}},
        )
