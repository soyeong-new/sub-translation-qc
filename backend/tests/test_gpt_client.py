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
async def test_verify_and_refine_sends_korean_and_target_text():
    payload = {"findings": [{"segment_id": "p1", "category": "mistranslation",
                              "corrected_text": "texto final", "description": "정확성 보완"}]}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "안녕", "target_text": "hola"}],
        profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="줄당 50자 이내",
    )
    assert result == payload["findings"]
    sent_user = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "hola" in sent_user


@pytest.mark.asyncio
async def test_verify_and_refine_does_not_mention_a_prior_correction_pass():
    """앵커링 편향 방지: GPT는 Claude가 뭘 했는지 몰라야 독립적으로 판단할 수
    있다 — 프롬프트에 "1차"/"이전 교정" 같은 언급이 없어야 한다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "1차" not in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_includes_pending_sensitive_hits_in_prompt():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={},
        pending_sensitive_hits=[{"segment_id": "p1", "term": "미친"}],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "미친" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_includes_extra_instruction_in_prompt_when_given():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
        extra_instruction="직역투를 더 강하게 잡아줘",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "직역투를 더 강하게 잡아줘" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_forbids_skipping_when_extra_instruction_given():
    """재질문(다시 질문하기)은 검수자가 이미 문제로 지적한 단건이라, 배치
    검증용 "애매하면 findings에서 빼라" 지시가 그대로 남아있으면 검수자
    지시사항과 충돌해 빈 응답이 나올 수 있다(회귀: 사용자 재현 — 재질문해도
    제안이 그대로였음) — extra_instruction이 있으면 스킵 지시가 빠지고
    반드시 포함하라는 지시로 바뀌어야 한다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
        extra_instruction="더 격식있게 다시 써줘",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "절대 응답 배열에 포함하지 마라" not in sent_system
    assert "findings에 포함하지 마라" not in sent_system
    assert "findings에서 빼는 것은 금지" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_warns_not_to_trust_own_prior_suggestion_when_extra_instruction_given():
    """claude_client와 동일한 회귀 방지(사용자 재현) — 재질문 target_text는
    이전 제안이라는 것과, 그렇다고 이미 맞다고 여기지 말라는 경고가 필요하다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
        extra_instruction="문장의 의미를 제대로 파악할 것",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "이미 한 번 고친 결과물" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_keeps_skip_clean_instruction_without_extra_instruction():
    """배치 검증(extra_instruction 없음)에서는 기존 "애매하면 빼라" 지시가
    그대로 유지돼야 한다 — 재질문 전용 문구로 바뀌면 배치 검증 때 대량
    오탐이 생긴다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "절대 응답 배열에 포함하지 마라" in sent_system
    assert "findings에서 빼는 것은 금지" not in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_uses_json_schema_response_format_with_required_fields():
    """segment_id 등 필드가 프롬프트 지시만으로는 가끔 누락돼 실제로 검증
    결과가 통째로 스킵된 사례가 있었다 — API가 스키마로 필드 존재를 강제해야
    한다(프롬프트 지시만으로는 강제가 안 됨)."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"]["type"] == "json_schema"
    item_schema = call_kwargs["response_format"]["json_schema"]["schema"][
        "properties"]["findings"]["items"]
    assert set(item_schema["required"]) == {
        "segment_id", "category", "corrected_text", "description"}


@pytest.mark.asyncio
async def test_verify_and_refine_requires_back_translation_when_extra_instruction_given():
    """재질문(다시 질문하기) 후에는 검수자가 보는 역번역도 새 corrected_text에
    맞춰 갱신돼야 한다 — 별도 교차모델 API 호출 대신, 같은 응답에 back_translation
    필드를 함께 요청해 한 번의 호출로 끝낸다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
        extra_instruction="더 격식있게 다시 써줘",
    )
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    item_schema = call_kwargs["response_format"]["json_schema"]["schema"][
        "properties"]["findings"]["items"]
    assert "back_translation" in item_schema["required"]
    sent_system = call_kwargs["messages"][0]["content"]
    assert "back_translation" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_omits_back_translation_without_extra_instruction():
    """배치 검증(extra_instruction 없음)에서는 back_translation을 요구하지
    않는다 — 배치 파이프라인은 이미 교차모델 역번역(pipeline.py)을 별도로
    쓰고 있어 여기서 추가하면 불필요한 중복이다."""
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    item_schema = call_kwargs["response_format"]["json_schema"]["schema"][
        "properties"]["findings"]["items"]
    assert "back_translation" not in item_schema["required"]


def _fake_response(payload: dict):
    fake_message = MagicMock()
    fake_message.content = json.dumps(payload)
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    return fake_response


@pytest.mark.asyncio
async def test_verify_and_refine_retries_when_corrected_text_leaks_korean():
    """회귀(실측 버그): 배치 처리 중 모델이 다른 항목의 korean_text를 착각해
    corrected_text에 그대로 옮기는 사례가 있었다 — 프롬프트 지시만으로는
    못 막으니 응답 내용 자체를 검증해 한국어가 새면 그 항목만 재요청해야
    한다. "다시 질문"도 이 함수를 그대로 재사용하므로 이 방어선 하나로
    양쪽 다 보호된다."""
    leaked = {"findings": [{"segment_id": "p1", "category": "mistranslation",
                             "corrected_text": "기사가 있는 리무진을 가졌어",
                             "description": "정확성 보완"}]}
    fixed = {"findings": [{"segment_id": "p1", "category": "mistranslation",
                            "corrected_text": "Tengo una limusina con chofer",
                            "description": "정확성 보완"}]}
    client = GptClient(api_key="fake", model="gpt-test")
    client._sdk_client.chat.completions.create = AsyncMock(
        side_effect=[_fake_response(leaked), _fake_response(fixed)])
    result = await client.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "기사가 있는 리무진을 가졌어", "target_text": "hola"}],
        profile={}, pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    assert result == fixed["findings"]
    assert client._sdk_client.chat.completions.create.call_count == 2
    retry_user = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert json.loads(retry_user) == [{"id": "p1", "korean_text": "기사가 있는 리무진을 가졌어",
                                        "target_text": "hola"}]


@pytest.mark.asyncio
async def test_verify_and_refine_flags_description_when_retry_still_leaks_korean():
    leaked = {"findings": [{"segment_id": "p1", "category": "mistranslation",
                             "corrected_text": "기사가 있는 리무진을 가졌어",
                             "description": "정확성 보완"}]}
    client = GptClient(api_key="fake", model="gpt-test")
    client._sdk_client.chat.completions.create = AsyncMock(
        side_effect=[_fake_response(leaked), _fake_response(leaked)])
    result = await client.verify_and_refine(
        pairs=[{"id": "p1", "korean_text": "기사가 있는 리무진을 가졌어", "target_text": "hola"}],
        profile={}, pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    assert "직접 재확인 필요" in result[0]["description"]


@pytest.mark.asyncio
async def test_verify_and_refine_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.verify_and_refine([], {}, [], "", "")


@pytest.mark.asyncio
async def test_verify_and_refine_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.verify_and_refine([], {}, [], "", "")


def _make_client_with_fake_transcribe(words):
    client = GptClient(api_key="fake", model="gpt-test", transcribe_model="whisper-1")
    fake_response = MagicMock()
    fake_response.words = words
    client._sdk_client.audio.transcriptions.create = AsyncMock(return_value=fake_response)
    return client


@pytest.mark.asyncio
async def test_transcribe_returns_words_with_timecodes(tmp_path):
    words = [SimpleNamespace(start=0.0, end=2.0, word="안녕하세요")]
    client = _make_client_with_fake_transcribe(words)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    result = await client.transcribe(str(audio_path))
    assert result == [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]


@pytest.mark.asyncio
async def test_transcribe_sends_korean_language_hint_and_configured_model(tmp_path):
    words = [SimpleNamespace(start=0.0, end=1.0, word="안녕")]
    client = _make_client_with_fake_transcribe(words)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    await client.transcribe(str(audio_path))
    kwargs = client._sdk_client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["language"] == "ko"
    assert kwargs["model"] == "whisper-1"
    assert kwargs["timestamp_granularities"] == ["word"]


@pytest.mark.asyncio
async def test_transcribe_returns_empty_list_when_no_words(tmp_path):
    """회귀(Finding #1): transcribe는 이제 청크 단위로 호출되므로, 단어가
    없는 조각(무음 구간 등)은 그 자체로는 실패가 아니다 — 빈 리스트를
    돌려주고, "에피소드 전체에 대사가 없음" 판단은
    pipeline._transcribe_in_chunks가 모든 조각을 병합한 뒤에 한다."""
    client = _make_client_with_fake_transcribe([])
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio-bytes")
    result = await client.transcribe(str(audio_path))
    assert result == []


@pytest.mark.asyncio
async def test_verify_and_refine_uses_profile_language_and_variant_in_prompt():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={"language": "es", "variant": "LATAM"},
        pending_sensitive_hits=[], knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "es(LATAM)" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_includes_naturalness_instruction_from_profile():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    profile = {
        "language": "es", "variant": "LATAM",
        "naturalness_check": {"llm_instruction": "직역투를 한국어 어순과 대조해 찾아라"},
    }
    await client.verify_and_refine(
        pairs=[], profile=profile, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "직역투를 한국어 어순과 대조해 찾아라" in sent_system


@pytest.mark.asyncio
async def test_verify_and_refine_falls_back_when_profile_empty():
    client = _make_client_with_fake_sdk(json.dumps({"findings": []}))
    await client.verify_and_refine(
        pairs=[], profile={}, pending_sensitive_hits=[],
        knowledge="", format_constraint="",
    )
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "대상언어" in sent_system


@pytest.mark.asyncio
async def test_back_translate_returns_korean_text_per_id():
    payload = {"results": [{"id": "p1", "korean_text": "안녕하세요"}]}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.back_translate(
        texts=[{"id": "p1", "text": "hola"}], profile={"language": "es", "variant": "LATAM"},
    )
    assert result == payload["results"]


@pytest.mark.asyncio
async def test_back_translate_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.back_translate([], {})


@pytest.mark.asyncio
async def test_back_translate_uses_json_schema_response_format_with_required_fields():
    """original_korean_text 등 필드가 프롬프트 지시만으로는 가끔 누락돼 검수
    화면에서 원문 역번역이 통째로 안 뜨는 사례가 있었다 — API가 스키마로
    필드 존재를 강제해야 한다(correct_primary/verify_and_refine과 동일한
    이유, 같은 방식)."""
    client = _make_client_with_fake_sdk(json.dumps({"results": []}))
    await client.back_translate(texts=[], profile={})
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"]["type"] == "json_schema"
    item_schema = call_kwargs["response_format"]["json_schema"]["schema"][
        "properties"]["results"]["items"]
    assert set(item_schema["required"]) == {
        "id", "korean_text", "original_korean_text", "is_improvement"}


@pytest.mark.asyncio
async def test_resolve_gender_from_context_sends_candidate_words_and_returns_results():
    payload = {"results": [{"id": "p1", "words": [
        {"index": 0, "is_person": True, "group_id": 0, "gender": "male", "referent": "Juan"},
    ]}]}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.resolve_gender_from_context(
        items=[{"id": "p1", "target_text": "Juan está cansado.",
                "korean_text": "후안이 피곤해해.", "candidate_words": ["cansado"]}],
        profile={"language": "es", "variant": "LATAM"},
    )
    assert result == payload["results"]
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"]["json_schema"]["name"] == "gender_resolution"
    sent_user = call_kwargs["messages"][1]["content"]
    assert "cansado" in sent_user
    assert "후안이 피곤해해" in sent_user


@pytest.mark.asyncio
async def test_resolve_gender_from_context_schema_requires_character_name_field():
    """character_name이 스키마에 없으면 OpenAI structured output이 이
    필드를 절대 채워 보내지 않는다 — 스키마에 명시적으로 있어야 한다."""
    payload = {"results": [{"id": "p1", "words": [
        {"index": 0, "is_person": True, "group_id": 0, "gender": "female",
         "referent": "특정 인물의 이름", "character_name": "성경"},
    ]}]}
    client = _make_client_with_fake_sdk(json.dumps(payload))
    result = await client.resolve_gender_from_context(
        items=[{"id": "p1", "target_text": "Seong-gyeong está cansada.",
                "korean_text": "성경이 피곤해해.", "candidate_words": ["cansada"]}],
        profile={"language": "es", "variant": "LATAM"},
    )
    assert result[0]["words"][0]["character_name"] == "성경"
    call_kwargs = client._sdk_client.chat.completions.create.call_args.kwargs
    word_schema = call_kwargs["response_format"]["json_schema"]["schema"][
        "properties"]["results"]["items"]["properties"]["words"]["items"]
    assert "character_name" in word_schema["properties"]
    assert "character_name" in word_schema["required"]


@pytest.mark.asyncio
async def test_resolve_gender_from_context_raises_on_malformed_json():
    client = _make_client_with_fake_sdk("JSON 아님")
    with pytest.raises(ValueError):
        await client.resolve_gender_from_context(items=[], profile={})


@pytest.mark.asyncio
async def test_resolve_gender_from_context_raises_on_empty_choices():
    client = _make_client_with_fake_sdk("무시됨")
    client._sdk_client.chat.completions.create.return_value.choices = []
    with pytest.raises(ValueError):
        await client.resolve_gender_from_context(items=[], profile={})


@pytest.mark.asyncio
async def test_apply_formality_uses_instruction_from_profile():
    """스페인어 tú/usted가 코드에 하드코딩돼 있으면 다른 언어 프로파일이
    적용되지 않는다 — profile의 formality_instruction을 프롬프트에 그대로
    실어 보내야 언어별로 다른 격식 활용 규칙을 줄 수 있다."""
    client = _make_client_with_fake_sdk(json.dumps({"results": []}))
    profile = {
        "language": "pt", "variant": "BR",
        "formality_instruction": "informal이면 você 활용형으로, formal이면 o senhor/a senhora 활용형으로.",
    }
    await client.apply_formality(items=[], profile=profile)
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "você" in sent_system
    assert "tú" not in sent_system


@pytest.mark.asyncio
async def test_apply_formality_falls_back_to_default_instruction_when_profile_empty():
    """profile={}(테스트 더미)로 호출해도 예외 없이 동작해야 한다 — 기존
    테스트들이 이 계약에 의존한다."""
    client = _make_client_with_fake_sdk(json.dumps({"results": []}))
    await client.apply_formality(items=[], profile={})
    sent_system = client._sdk_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "tú" in sent_system
