"""Claude/GPT 두 모델을 동시 호출해 결과를 하나의 finding 리스트로 병합하는 모듈."""

import asyncio
import logging
from typing import Coroutine, List

logger = logging.getLogger(__name__)


async def call_both(label_a: str, coro_a: Coroutine, label_b: str, coro_b: Coroutine) -> List[dict]:
    """두 모델 호출을 동시에 실행하고 결과를 병합한다.

    한쪽이 예외를 던져도 성공한 쪽 결과만으로 계속 진행한다 (design
    §프로바이더 세부 동작 — 앞단계 비용이 이미 들었으므로 전체 재시도보다
    부분 결과 반영이 낫다). 둘 다 실패하면 빈 리스트를 반환한다."""
    result_a, result_b = await asyncio.gather(coro_a, coro_b, return_exceptions=True)

    merged: List[dict] = []
    for label, result in ((label_a, result_a), (label_b, result_b)):
        if isinstance(result, Exception):
            logger.warning("%s 앙상블 호출 실패: %s", label, result)
            continue
        for item in result:
            merged.append({**item, "model": label})
    return merged
