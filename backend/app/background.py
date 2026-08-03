"""asyncio 기반 백그라운드 분석 실행: FastAPI 프로세스 안에서 create_task로 돈다.

기존 arq 워커는 job_timeout 초과 시 asyncio.CancelledError로 태스크를 취소했고
(BaseException 상속이라 일반 except Exception으로 못 잡음), 그래서 별도의
이중 처리가 필요했다. asyncio.wait_for는 다르다 — 타임아웃이 지나면
asyncio.TimeoutError를 "던지고" 내부 태스크를 취소하지만, wait_for를 호출한
쪽(여기)에는 TimeoutError가 일반 예외로 전달된다. CancelledError 특유의
이중 처리가 필요 없다."""

import asyncio
import logging
from app.db import async_session
from app.models import TargetVersion, Episode
from app.core.pipeline import run_pipeline
from app.repositories import save_pipeline_result
from app.providers.base import get_provider

logger = logging.getLogger(__name__)

ANALYSIS_TIMEOUT_SECONDS = 3600


async def analyze_and_save(target_version_id: str, target_srt_path: str) -> None:
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            episode = await session.get(Episode, tv.episode_id)

        provider = get_provider()
        result = await asyncio.wait_for(
            run_pipeline(
                video_path=episode.video_path,
                target_srt_path=target_srt_path,
                language=tv.target_language, variant=tv.variant,
                target_version_id=target_version_id, provider=provider,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

        async with async_session() as session:
            await save_pipeline_result(session, target_version_id, result)
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "review"
            tv.video_proxy_path = result.get("video_proxy_path")
            await session.commit()
    except asyncio.TimeoutError:
        logger.warning("analyze_and_save 타임아웃 (target_version_id=%s)", target_version_id)
        await _mark_failed(target_version_id, "분석 시간 초과 (1시간)")
    except Exception as exc:
        logger.exception("analyze_and_save 실패 (target_version_id=%s)", target_version_id)
        await _mark_failed(target_version_id, str(exc))


async def _mark_failed(target_version_id: str, message: str) -> None:
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            if tv is not None:
                tv.status = "failed"
                tv.error_message = message
                await session.commit()
    except Exception:
        logger.exception(
            "실패 상태 기록 중 추가 오류 (target_version_id=%s)", target_version_id,
        )
