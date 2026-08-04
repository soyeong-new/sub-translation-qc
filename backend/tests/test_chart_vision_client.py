import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.chart_vision_client import ChartVisionClient, get_chart_vision_client
from app.providers.base import ProviderNotConfiguredError


def _make_client_with_fake_sdk(response_text: str) -> ChartVisionClient:
    client = ChartVisionClient(api_key="fake", model="claude-test")
    fake_block = MagicMock()
    fake_block.text = response_text
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    client._sdk_client.messages.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_extract_chart_parses_characters_and_relationships(tmp_path):
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"fake png bytes")
    payload = {
        "characters": [{"label": "민지", "suggested_gender": "female"}],
        "relationships": [{"speaker_label": "민지", "addressee_label": "서준",
                            "relationship_type": "연인"}],
    }
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.extract_chart(str(image_path))
    assert result == payload


@pytest.mark.asyncio
async def test_extract_chart_sends_base64_image_and_correct_media_type(tmp_path):
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"fake png bytes")
    client = _make_client_with_fake_sdk(json.dumps({"characters": [], "relationships": []}))
    await client.extract_chart(str(image_path))
    sent_messages = client._sdk_client.messages.create.call_args.kwargs["messages"]
    content_blocks = sent_messages[0]["content"]
    image_block = next(b for b in content_blocks if b["type"] == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["type"] == "base64"


@pytest.mark.asyncio
async def test_extract_chart_raises_on_malformed_json(tmp_path):
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"fake png bytes")
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.extract_chart(str(image_path))


@pytest.mark.asyncio
async def test_extract_chart_raises_when_keys_missing(tmp_path):
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"fake png bytes")
    client = _make_client_with_fake_sdk(json.dumps({"characters": []}))
    with pytest.raises(ValueError):
        await client.extract_chart(str(image_path))


def test_get_chart_vision_client_raises_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    with pytest.raises(ProviderNotConfiguredError):
        get_chart_vision_client()


def test_get_chart_vision_client_succeeds_when_env_vars_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test")
    client = get_chart_vision_client()
    assert isinstance(client, ChartVisionClient)
