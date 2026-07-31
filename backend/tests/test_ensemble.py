import asyncio
import pytest
from app.providers.ensemble import call_both


async def _ok(items):
    return items


async def _fail():
    raise RuntimeError("API 오류")


async def _cancelled():
    raise asyncio.CancelledError()


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


@pytest.mark.asyncio
async def test_base_exception_from_one_side_is_dropped_not_crashed():
    """asyncio.gather(..., return_exceptions=True)는 asyncio.CancelledError처럼
    Exception이 아니라 BaseException만 상속하는 예외도 값으로 돌려준다 (예:
    worker.py의 job_timeout 취소가 review_translation 호출 도중 발생하는
    경우). isinstance(result, Exception)만 검사하면 이런 결과가 `for item in
    result:`로 새어들어가 "'CancelledError' object is not iterable"
    TypeError로 크래시한다 — Exception이 아닌 BaseException으로 검사해야
    한쪽만 실패했을 때와 동일하게 다른 쪽 결과를 살릴 수 있다."""
    result = await call_both(
        "claude", _cancelled(),
        "gpt", _ok([{"segment_id": "p1", "description": "GPT 지적"}]),
    )
    assert len(result) == 1
    assert result[0]["model"] == "gpt"
