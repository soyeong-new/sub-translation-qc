import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.claude_client import ClaudeClient


def _make_client_with_fake_sdk(response_text: str) -> ClaudeClient:
    client = ClaudeClient(api_key="fake", model="claude-test")
    fake_block = MagicMock()
    fake_block.text = response_text
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    client._sdk_client.messages.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_review_translation_parses_json_array():
    payload = [{"segment_id": "p1", "category": "translation",
                "description": "오역", "suggested_text": "texto", "confidence": 0.8}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
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
async def test_review_translation_raises_on_empty_content():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.messages.create.return_value.content = []
    with pytest.raises(ValueError):
        await client.review_translation([], "", {}, "")


@pytest.mark.asyncio
async def test_check_sensitivity_parses_json_array():
    payload = [{"segment_id": "p1", "description": "민감어 문맥 확인 필요", "severity": "medium"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.check_sensitivity(
        [{"id": "p1", "target_text": "qué mierda"}], [{"segment_id": "p1", "term": "mierda"}],
    )
    assert result == payload


@pytest.mark.asyncio
async def test_check_sensitivity_raises_on_empty_content():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.messages.create.return_value.content = []
    with pytest.raises(ValueError):
        await client.check_sensitivity([], [])


@pytest.mark.asyncio
async def test_review_translation_raises_when_response_is_object_not_array():
    """프롬프트에 정확한 필드명을 명시했더니, Claude가 GPT식으로
    {"findings": [...]}처럼 최상위를 객체로 감싸서 응답할 가능성이 생겼다.
    Claude는 GPT와 달리 최상위가 배열이어야 하므로, 이 경우도 다른
    malformed-response 케이스와 동일하게 ValueError로 막아야 한다 — 그렇지
    않으면 ensemble.py에서 dict를 순회하다 TypeError로 크래시한다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    with pytest.raises(ValueError):
        await client.review_translation([], "", {}, "")
