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
async def test_correct_primary_uses_output_schema_with_required_fields():
    """segment_id 등 필드가 프롬프트 지시만으로는 가끔 누락돼 실제로 검증
    결과가 통째로 스킵된 사례가 있었다 — output_config.format으로 API가
    필드 존재를 강제해야 한다. tool_choice 강제 대신 이 방식을 쓰는 이유는
    thinking 모드와 충돌하지 않고 응답이 여전히 텍스트 블록으로 오기
    때문이다(기존 파싱 로직을 그대로 쓸 수 있음)."""
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    call_kwargs = client._sdk_client.messages.create.call_args.kwargs
    schema = call_kwargs["output_config"]["format"]["schema"]
    assert schema["type"] == "array"
    assert set(schema["items"]["required"]) == {
        "segment_id", "category", "corrected_text", "description"}


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
async def test_shrink_line_prompt_instructs_to_preserve_existing_gender_and_formality():
    """회귀(design 논의): 안전망 축약이 이미 승인된 문장을 다시 쓰면서
    성별/격식을 모르는 채 새로 생성해, 이미 맞게 고쳐져 있던 성별 어미가
    깨지는 사례가 실측으로 확인됐다. shrink_line은 profile을 안 받으니
    "지금 입력에 이미 반영된 성별/격식은 바꾸지 마라"는 지시로 방어한다."""
    client = _make_client_with_fake_sdk(json.dumps({"shrunk_text": "짧아진 문장"}))
    await client.shrink_line("문장", max_chars=50, max_lines=2)
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "성별" in sent_system and "격식" in sent_system


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


@pytest.mark.asyncio
async def test_correct_primary_uses_target_language_from_profile_not_spanish():
    """스페인어가 프롬프트에 하드코딩돼 있으면 다른 언어 프로파일로 호출해도
    Claude에게 "스페인어로서 자연스러운가"를 검증하라고 지시하게 된다 —
    profile의 언어가 그대로 반영돼야 한다."""
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={"language": "pt", "variant": "BR"},
        pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "스페인어" not in sent_system
    assert "pt(BR)" in sent_system


@pytest.mark.asyncio
async def test_back_translate_uses_target_language_from_profile_not_spanish():
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.back_translate(
        texts=[], profile={"language": "pt", "variant": "BR"},
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "스페인어" not in sent_system
