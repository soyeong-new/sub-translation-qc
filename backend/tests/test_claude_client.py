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
async def test_correct_primary_parses_json_array_of_changed_segments():
    payload = [{"segment_id": "p1", "category": "gender",
                "corrected_text": "está feliz", "description": "성별 일치 수정"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.correct_primary(
        pairs=[{"id": "p1", "korean_text": "안녕", "target_text": "esta feliz"}],
        profile={}, characters=[], relationships=[], pending_sensitive_hits=[],
        knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_correct_primary_includes_extra_instruction_in_prompt_when_given():
    payload = []
    client = _make_client_with_fake_sdk(json.dumps(payload))
    await client.correct_primary(
        pairs=[], profile={}, characters=[], relationships=[], pending_sensitive_hits=[],
        knowledge="", format_constraint="", extra_instruction="더 격식있게 고쳐줘",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "더 격식있게 고쳐줘" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], [], [], "", "")


@pytest.mark.asyncio
async def test_correct_primary_raises_on_empty_content():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.messages.create.return_value.content = []
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], [], [], "", "")


@pytest.mark.asyncio
async def test_correct_primary_raises_when_response_is_object_not_array():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    with pytest.raises(ValueError):
        await client.correct_primary([], {}, [], [], [], "", "")


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
        characters=[], relationships=[], pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "es(LATAM)" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_includes_grammar_agreement_instruction_from_profile():
    client = _make_client_with_fake_sdk(json.dumps([]))
    profile = {
        "language": "es", "variant": "LATAM",
        "grammar_agreement": {"llm_instruction": "형용사 성별 일치를 확인하라"},
    }
    await client.correct_primary(
        pairs=[], profile=profile, characters=[], relationships=[],
        pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "형용사 성별 일치를 확인하라" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_falls_back_when_profile_empty():
    """profile={}(테스트 더미)로 호출해도 예외 없이 동작해야 한다 — 기존
    테스트들이 이 계약에 의존한다."""
    client = _make_client_with_fake_sdk(json.dumps([]))
    await client.correct_primary(
        pairs=[], profile={}, characters=[], relationships=[],
        pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.messages.create.call_args.kwargs["system"]
    assert "대상언어" in sent_system


@pytest.mark.asyncio
async def test_correct_primary_handles_none_valued_grammar_agreement_section():
    """YAML에 grammar_agreement: 처럼 섹션 헤더만 있고 본문이 없으면 PyYAML이
    None으로 파싱한다. profile.get("grammar_agreement", {})는 키가 존재하는
    경우(None이라도) 기본값 {}를 쓰지 않으므로 None.get(...)에서 AttributeError가
    난다 — 이를 방지하는 회귀 테스트."""
    payload = [{"segment_id": "p1", "category": "gender",
                "corrected_text": "está feliz", "description": "성별 일치 수정"}]
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.correct_primary(
        pairs=[{"id": "p1", "korean_text": "안녕", "target_text": "esta feliz"}],
        profile={"language": "es", "variant": "LATAM", "grammar_agreement": None},
        characters=[], relationships=[], pending_sensitive_hits=[],
        knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload
