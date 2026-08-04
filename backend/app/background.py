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
from app.core.ingest import delete_original_video
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
        cached_segments = (
            episode.stt_cache.get("segments") if episode.stt_cache else None
        )
        result = await asyncio.wait_for(
            run_pipeline(
                video_path=episode.video_path,
                target_srt_path=target_srt_path,
                language=tv.target_language, variant=tv.variant,
                target_version_id=target_version_id, provider=provider,
                cached_korean_segments=cached_segments,
                cached_video_proxy_path=episode.video_proxy_path,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

        async with async_session() as session:
            await save_pipeline_result(session, target_version_id, result)
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "review"
            tv.video_proxy_path = result.get("video_proxy_path")
            tv.warnings = result.get("warnings") or None
            episode_row = await session.get(Episode, tv.episode_id)
            if episode_row.stt_cache is None:
                # 최초 성공 시에만 캐시를 채운다 — 이미 캐시가 있으면(다른
                # target_version이 먼저 채웠거나 재시도인 경우) 덮어쓰지 않는다.
                episode_row.stt_cache = {"segments": result.get("korean_segments_raw", [])}
                episode_row.video_proxy_path = result.get("video_proxy_path")
            await session.commit()

        # 원본 영상은 결과가 전부 커밋된 뒤에만 지운다. run_pipeline은 원본을
        # 지우지 않고 그대로 둔다 — video_proxy_path가 DB에 영속화되기 전에
        # 원본부터 지워버리면, 그 사이 어딘가(analyze_characters 실패, 타임아웃,
        # 프로세스 크래시 등)에서 죽었을 때 원본도 없고 프록시 경로도 저장되지
        # 않아 생성된 프록시 파일이 고아로 남고, /run-analysis 재시도도 영영
        # 실패하게 된다(필요한 원본 영상이 이미 없으므로).
        #
        # 이 삭제 자체는 별도로 감싼다 — 이미 status="review"로 커밋까지 끝난
        # 뒤라, 여기서 예외(예: 권한 문제로 unlink가 FileNotFoundError 아닌
        # OSError를 던지는 경우)가 바깥 except로 새어나가 _mark_failed를
        # 호출하면 이미 성공한 분석 결과를 "실패"로 덮어써버리게 된다.
        try:
            delete_original_video(episode.video_path)
        except Exception:
            logger.exception(
                "원본 영상 삭제 실패, 분석 결과는 이미 저장됨 (target_version_id=%s)",
                target_version_id,
            )
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
