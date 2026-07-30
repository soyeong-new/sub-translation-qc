import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.gpt_client import GptClient


def _make_client_with_fake_sdk(response_text: str) -> GptClient:
    client = GptClient(api_key="fake", model="gpt-test")
    fake_message = MagicMock()
    fake_message.content = response_text
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    client._sdk_client.chat.completions.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_review_translation_parses_json_array():
    payload = [{"segment_id": "p1", "category": "translation",
                "description": "오역", "suggested_text": "texto", "confidence": 0.8}]
    client = _make_client_with_fake_sdk(json.dumps({"findings": payload}))
    result = await client.review_translation(
        [{"id": "p1", "korean_text": "안녕", "target_text": "hola"}],
        "지식베이스", {}, "줄당 50자 이내",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_review_translation_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.review_translation([], "", {}, "")


@pytest.mark.asyncio
async def test_review_translation_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.review_translation([], "", {}, "")


@pytest.mark.asyncio
async def test_check_sensitivity_parses_json_array():
    payload = [{"segment_id": "p1", "description": "민감어 문맥 확인 필요", "severity": "medium"}]
    client = _make_client_with_fake_sdk(json.dumps({"findings": payload}))
    result = await client.check_sensitivity(
        [{"id": "p1", "target_text": "qué mierda"}], [{"segment_id": "p1", "term": "mierda"}],
    )
    assert result == payload


@pytest.mark.asyncio
async def test_check_sensitivity_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.check_sensitivity([], [])
