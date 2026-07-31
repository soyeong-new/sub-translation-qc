"""arq 백그라운드 작업 정의: 실제 분석 파이프라인 실행과 큐 등록."""

import logging
import os
from arq.connections import RedisSettings
from app.db import async_session
from app.models import TargetVersion, Episode
from app.core.pipeline import run_pipeline
from app.repositories import save_pipeline_result
from app.providers.base import get_provider

logger = logging.getLogger(__name__)


async def run_analysis_job(ctx, target_version_id: str, target_srt_path: str) -> None:
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            episode = await session.get(Episode, tv.episode_id)

        provider = get_provider()
        result = await run_pipeline(
            korean_audio_path=episode.video_path,
            target_srt_path=target_srt_path,
            language=tv.target_language, variant=tv.variant,
            target_version_id=target_version_id, provider=provider,
        )

        async with async_session() as session:
            await save_pipeline_result(session, target_version_id, result)
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "review"
            await session.commit()
    except Exception as exc:
        # 실패 상태 기록 자체도 실패할 수 있다 (예: target_version_id가 그 사이
        # 삭제되어 session.get이 None을 돌려주는 경우, 혹은 DB 연결 문제가 원래
        # 예외의 원인이었던 경우). 이 블록에서 새 예외가 밖으로 새어나가면
        # run_analysis_job의 "예외를 재발생시키지 않는다"는 계약이 깨지고,
        # arq job runner가 죽으며 target_version.status가 "analyzing"에
        # 영원히 멈춰버린다. 그래서 이 블록 자체를 다시 try/except로 감싼다.
        try:
            async with async_session() as session:
                tv = await session.get(TargetVersion, target_version_id)
                if tv is not None:
                    tv.status = "failed"
                    tv.error_message = str(exc)
                    await session.commit()
        except Exception:
            logger.exception(
                "run_analysis_job 실패 상태 기록 중 추가 오류 (target_version_id=%s)",
                target_version_id,
            )


async def enqueue_analysis(redis_pool, target_version_id: str, target_srt_path: str) -> None:
    await redis_pool.enqueue_job("run_analysis_job", target_version_id, target_srt_path)


class WorkerSettings:
    functions = [run_analysis_job]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379"))
