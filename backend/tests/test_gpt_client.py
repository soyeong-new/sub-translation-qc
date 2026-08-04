import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.providers.gpt_client import GptClient


def _make_client_with_fake_sdk(response_text) -> GptClient:
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
async def test_verify_and_refine_sends_current_text_and_original_reference():
    payload = {"findings": [{"segment_id": "p1", "category": "translation",
                              "corrected_text": "texto final", "description": "정확성 보완"}]}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "안녕", "current_text": "hola corregido"}],
        original_target_by_id={"p1": "hola original"},
        profile={}, knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload["findings"]
    sent_user = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "hola corregido" in sent_user
    assert "hola original" in sent_user


@pytest.mark.asyncio
async def test_verify_and_refine_falls_back_to_current_text_when_no_original_recorded():
    payload = {"findings": []}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    await client.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "안녕", "current_text": "hola sin cambios"}],
        original_target_by_id={}, profile={}, knowledge="", format_constraint="",
    )
    sent_user = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert sent_user.count("hola sin cambios") == 2


@pytest.mark.asyncio
async def test_verify_and_refine_includes_extra_instruction_in_prompt_when_given():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], original_target_by_id={}, profile={}, knowledge="", format_constraint="",
        extra_instruction="직역투를 더 강하게 잡아줘",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "직역투를 더 강하게 잡아줘" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.verify_and_refine([], {}, {}, "", "")


@pytest.mark.asyncio
async def test_verify_and_refine_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.verify_and_refine([], {}, {}, "", "")


def _make_client_with_fake_transcribe(segments):
    client = GptClient(api_key="fake", model="gpt-test", transcribe_model="whisper-1")
    fake_response = MagicMock()
    fake_response.segments = segments
    client._sdk_client.audio.transcriptions.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_transcribe_returns_segments_with_timecodes(tmp_path):
    segments = [SimpleNamespace(start=0.0, end=2.0, text="안녕하세요")]
    client = _make_client_with_fake_transcribe(segments)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    result = await client.transcribe(str(audio_path))
    assert result == [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]


@pytest.mark.asyncio
async def test_transcribe_sends_korean_language_hint_and_configured_model(tmp_path):
    segments = [SimpleNamespace(start=0.0, end=1.0, text="안녕")]
    client = _make_client_with_fake_transcribe(segments)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    await client.transcribe(str(audio_path))
    kwargs = client._sdk_client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["language"] == "ko"
    assert kwargs["model"] == "whisper-1"


@pytest.mark.asyncio
async def test_transcribe_raises_when_no_segments(tmp_path):
    client = _make_client_with_fake_transcribe([])
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    with pytest.raises(ValueError):
        await client.transcribe(str(audio_path))


@pytest.mark.asyncio
async def test_analyze_characters_parses_object_response():
    payload = {"characters": [{"label": "민수", "gendered_segment_ids": ["p1"]}],
               "relationships": []}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.analyze_characters(
        [{"id": "p1", "target_text": "hola"}], {"checks_enabled": {"gender_agreement": True}})
    assert result == payload


@pytest.mark.asyncio
async def test_analyze_characters_raises_when_keys_missing():
    client = _make_client_with_fake_sdk(json.dumps({"foo": "bar"}))
    with pytest.raises(ValueError):
        await client.analyze_characters([], {})


@pytest.mark.asyncio
async def test_analyze_characters_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.analyze_characters([], {})
