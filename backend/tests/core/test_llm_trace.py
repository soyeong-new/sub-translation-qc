import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core import llm_trace


def _rows(tmp_path, tv="tv1"):
    f = tmp_path / f"{tv}.llm.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_record_writes_raw_input_and_output_for_the_current_run(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_trace, "TRACE_DIR", tmp_path)
    token = llm_trace.trace_id.set("tv1")
    try:
        llm_trace.record("gpt", "격식 반영", "gpt-test", "시스템", "사용자", '{"results": []}',
                         usage={"in": 10, "out": 2})
    finally:
        llm_trace.trace_id.reset(token)
    row = _rows(tmp_path)[0]
    assert (row["provider"], row["label"], row["model"]) == ("gpt", "격식 반영", "gpt-test")
    assert row["system"] == "시스템" and row["user"] == "사용자" and row["output"] == '{"results": []}'
    assert row["usage"] == {"in": 10, "out": 2} and "time" in row


def test_record_skips_calls_outside_a_run_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_trace, "TRACE_DIR", tmp_path)
    llm_trace.record("gpt", "x", "m", "s", "u", "o")          # trace_id 없음 → 기록 안 함
    assert list(tmp_path.iterdir()) == []
    blocker = tmp_path / "f"; blocker.write_text("x")
    monkeypatch.setattr(llm_trace, "TRACE_DIR", blocker)       # 디렉터리를 만들 수 없는 경로
    token = llm_trace.trace_id.set("tv1")
    try:
        llm_trace.record("gpt", "x", "m", "s", "u", "o")        # 예외가 새어 나오면 안 된다
    finally:
        llm_trace.trace_id.reset(token)


@pytest.mark.asyncio
async def test_gpt_client_call_records_raw_response(tmp_path, monkeypatch):
    from app.providers.gpt_client import GptClient
    monkeypatch.setattr(llm_trace, "TRACE_DIR", tmp_path)
    client = GptClient(api_key="fake", model="gpt-test")
    msg = MagicMock(); msg.content = '{"results": [{"id": "a", "corrected_text": "x"}]}'
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=11, completion_tokens=3)
    client._sdk_client.chat.completions.create = AsyncMock(return_value=resp)
    token = llm_trace.trace_id.set("tv1")
    try:
        await client._call("SYS", "USER", key="results", label="격식 반영")
    finally:
        llm_trace.trace_id.reset(token)
    row = _rows(tmp_path)[0]
    assert row["label"] == "격식 반영" and row["system"] == "SYS" and row["user"] == "USER"
    assert row["output"] == msg.content and row["model"] == "gpt-test"
    assert row["usage"] == {"input_tokens": 11, "output_tokens": 3}


@pytest.mark.asyncio
async def test_gpt_client_records_raw_text_even_when_response_is_not_valid_json(tmp_path, monkeypatch):
    from app.providers.gpt_client import GptClient
    monkeypatch.setattr(llm_trace, "TRACE_DIR", tmp_path)
    client = GptClient(api_key="fake", model="gpt-test")
    msg = MagicMock(); msg.content = "```json 깨진 응답"
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    client._sdk_client.chat.completions.create = AsyncMock(return_value=resp)
    token = llm_trace.trace_id.set("tv1")
    try:
        with pytest.raises(ValueError):
            await client._call("S", "U", key="results")
    finally:
        llm_trace.trace_id.reset(token)
    assert _rows(tmp_path)[0]["output"] == "```json 깨진 응답"
