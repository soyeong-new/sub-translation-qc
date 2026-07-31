"""arq 백그라운드 작업 정의: 실제 분석 파이프라인 실행과 큐 등록."""

import os
from arq.connections import RedisSettings
from app.db import async_session
from app.models import TargetVersion, Episode
from app.core.pipeline import run_pipeline
from app.repositories import save_pipeline_result
from app.providers.base import get_provider


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
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "failed"
            tv.error_message = str(exc)
            await session.commit()


async def enqueue_analysis(redis_pool, target_version_id: str, target_srt_path: str) -> None:
    await redis_pool.enqueue_job("run_analysis_job", target_version_id, target_srt_path)


class WorkerSettings:
    functions = [run_analysis_job]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379"))
