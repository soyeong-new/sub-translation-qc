import pytest
from app.providers.ensemble import call_both


async def _ok(items):
    return items


async def _fail():
    raise RuntimeError("API 오류")


@pytest.mark.asyncio
async def test_both_succeed_tags_and_merges_results():
    result = await call_both(
        "claude", _ok([{"segment_id": "p1", "description": "클로드 지적"}]),
        "gpt", _ok([{"segment_id": "p2", "description": "GPT 지적"}]),
    )
    assert len(result) == 2
    by_model = {r["model"]: r for r in result}
    assert by_model["claude"]["segment_id"] == "p1"
    assert by_model["gpt"]["segment_id"] == "p2"


@pytest.mark.asyncio
async def test_partial_failure_keeps_successful_side():
    result = await call_both(
        "claude", _fail(),
        "gpt", _ok([{"segment_id": "p1", "description": "GPT 지적"}]),
    )
    assert len(result) == 1
    assert result[0]["model"] == "gpt"


@pytest.mark.asyncio
async def test_both_failing_returns_empty_list():
    result = await call_both("claude", _fail(), "gpt", _fail())
    assert result == []


@pytest.mark.asyncio
async def test_existing_model_key_is_not_overwritten():
    """항목이 이미 model 키를 가지고 있으면(방어적) call_both가 강제로
    자기 라벨로 덮어쓴다 — 호출자별 라벨이 항상 신뢰 가능한 출처여야 한다."""
    result = await call_both(
        "claude", _ok([{"segment_id": "p1", "description": "x", "model": "gpt"}]),
        "gpt", _ok([]),
    )
    assert result[0]["model"] == "claude"
