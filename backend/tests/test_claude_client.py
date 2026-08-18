import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.claude_client import ClaudeClient


def _make_client_with_fake_sdk(response_text: str) -> ClaudeClient:
    client = ClaudeClient(api_key="fake", model="claude-test")
    fake_block = MagicMock(spec=["type", "text"])
    fake_block.type = "text"
    fake_block.text = response_text
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    client._sdk_client.messages.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_correct_primary_parses_json_array_of_changed_segments():
    payload = [{"segment_id": "p1", "category": "sensitivity",
                "corrected_text": "está feliz", "description": "비속어 교정"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.correct_primary(
        pairs=[{"id": "p1", "korean_text": "안녕", "target_text": "esta feliz"}],
        profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_correct_primary_includes_extra_instruction_in_prompt_when_given():
    payload = []
    client = _make_client_with_fake_sdk(json.dumps(payload))
    await client.correct_primary(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="", extra_instruction="더 격식있게 고쳐줘",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "더 격식있게 고쳐줘" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], "", "")


@pytest.mark.asyncio
async def test_correct_primary_skips_thinking_block_before_text_block():
    """Claude Sonnet 5+는 thinking 파라미터를 안 주면 적응형 사고가 기본으로
    켜져, 복잡한 프롬프트에서 content[0]이 ThinkingBlock(.text 없음)이고
    실제 텍스트는 그 다음 블록에 온다. content[0]을 무조건 읽으면 깨진다."""
    payload = [{"segment_id": "p1", "category": "sensitivity",
                "corrected_text": "está feliz", "description": "비속어 교정"}]
    client = ClaudeClient(api_key="fake", model="claude-test")
    thinking_block = MagicMock(spec=["type", "thinking"])
    thinking_block.type = "thinking"
    thinking_block.thinking = ""
    text_block = MagicMock(spec=["type", "text"])
    text_block.type = "text"
    text_block.text = json.dumps(payload)
    fake_response = MagicMock()
    fake_response.content = [thinking_block, text_block]
    client._sdk_client.messages.create = AsyncMock(return_value=fake_response)

    result = await client.correct_primary(
        pairs=[{"id": "p1", "korean_text": "안녕", "target_text": "esta feliz"}],
        profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_correct_primary_raises_on_empty_content():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.messages.create.return_value.content = []
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], "", "")


@pytest.mark.asyncio
async def test_correct_primary_raises_when_response_is_object_not_array():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], "", "")


@pytest.mark.asyncio
async def test_shrink_line_returns_shrunk_text():
    client = _make_client_with_fake_sdk(json.dumps({"shrunk_text": "짧아진 문장"}))
    result = await client.shrink_line("아주 길어서 줄여야 하는 문장입니다", max_chars=50, max_lines=2)
    assert result == "짧아진 문장"


@pytest.mark.asyncio
async def test_shrink_line_raises_when_response_is_array_not_object():
    client = _make_client_with_fake_sdk(json.dumps(["짧아진 문장"]))
    with pytest.raises(ValueError):
        await client.shrink_line("문장", max_chars=50, max_lines=2)


@pytest.mark.asyncio
async def test_shrink_line_raises_on_empty_content():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.messages.create.return_value.content = []
    with pytest.raises(ValueError):
        await client.shrink_line("문장", max_chars=50, max_lines=2)


@pytest.mark.asyncio
async def test_correct_primary_uses_profile_language_and_variant_in_prompt():
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={"language": "es", "variant": "LATAM"},
        pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "es(LATAM)" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_falls_back_when_profile_empty():
    """profile={}(테스트 더미)로 호출해도 예외 없이 동작해야 한다 — 기존
    테스트들이 이 계약에 의존한다."""
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={},
        pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "대상언어" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_does_not_mention_a_second_pass_reviewer():
    """이제 Claude는 비속어만이 아니라 번역 전반을 독립적으로 검증한다 —
    "2차 검수자의 몫" 같은 스코프 제한 문구가 남아있으면 안 된다."""
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "2차" not in sent_system


@pytest.mark.asyncio
async def test_back_translate_returns_korean_text_per_id():
    payload = [{"id": "p1", "korean_text": "안녕하세요"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.back_translate(
        texts=[{"id": "p1", "text": "hola"}], profile={"language": "es", "variant": "LATAM"},
    )
    assert result == payload


@pytest.mark.asyncio
async def test_back_translate_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.back_translate([], {})
