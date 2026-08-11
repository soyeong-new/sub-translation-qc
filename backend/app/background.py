"""asyncio 기반 백그라운드 분석 실행: FastAPI 프로세스 안에서 create_task로 돈다.

기존 arq 워커는 job_timeout 초과 시 asyncio.CancelledError로 태스크를 취소했고
(BaseException 상속이라 일반 except Exception으로 못 잡음), 그래서 별도의
이중 처리가 필요했다. asyncio.wait_for는 다르다 — 타임아웃이 지나면
asyncio.TimeoutError를 "던지고" 내부 태스크를 취소하지만, wait_for를 호출한
쪽(여기)에는 TimeoutError가 일반 예외로 전달된다. CancelledError 특유의
이중 처리가 필요 없다."""

import asyncio
import logging
from sqlalchemy import select
from app.db import async_session
from app.models import TargetVersion, Episode, Segment
from app.core.pipeline import (
    run_pipeline_phase1, run_pipeline_phase2,
    registers_need_confirmation, pairs_from_segments, resolved_registers_from_segments,
)
from app.core.pretreatment import find_pending_sensitive_hits
from app.core.ingest import delete_original_video
from app.repositories import save_phase1_result, save_phase2_result, get_suggested_not_applicable_lemmas
from app.providers.base import get_provider, ModelProvider
from app.language_profiles.loader import load_profile
from app.knowledge.loader import load_knowledge, load_sensitive_terms, load_profanity_dictionary

logger = logging.getLogger(__name__)

ANALYSIS_TIMEOUT_SECONDS = 3600

# episode.stt_cache에 이 값을 같이 저장해, 캐시가 지금 코드가 기대하는
# STT 결과 형태(단어 단위 타임코드)로 만들어졌는지 표시한다. transcribe()의
# 반환 단위가 바뀌면(문장→단어 전환이 그 예) 이 값을 올려서, 옛 형태로
# 저장된 캐시를 자동으로 무효화한다 — 사람이 DB를 직접 안 지워도 다음
# 분석 때 알아서 다시 STT를 돈다.
STT_CACHE_GRANULARITY = "word"


async def analyze_and_save(target_version_id: str, target_srt_path: str) -> None:
    """S1만 실행하고 저장한다. 성별/격식 확인이 필요한 줄이 하나라도 있으면
    거기서 멈추고 status="awaiting_confirmation"으로 사람을 기다린다 — 확인
    전에 S2(AI 검증)를 돌리면 그 줄의 성별/격식을 추측 없이는 정확히 검증할
    수 없기 때문이다. 확인이 필요 없으면(전부 자동 판정됐거나 애초에 성별/
    격식 표시가 없는 줄뿐이면) 곧장 이어서 S2까지 실행한다."""
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            episode = await session.get(Episode, tv.episode_id)
            suggested_not_applicable_lemmas = await get_suggested_not_applicable_lemmas(
                session, tv.target_language)

        provider = get_provider()
        # 캐시가 지금 기대하는 형태(단어 단위)로 저장된 게 아니면 무시하고
        # 다시 STT를 돈다 — granularity 태그 없는(옛 문장 단위) 캐시는
        # align()에 그대로 넣으면 여전히 동작은 하지만(문장 하나짜리 "단어"로
        # 취급됨) 이번에 고친 정밀 정렬의 이득을 못 본다.
        cached_segments = (
            episode.stt_cache.get("segments")
            if episode.stt_cache and episode.stt_cache.get("granularity") == STT_CACHE_GRANULARITY
            else None
        )
        phase1 = await asyncio.wait_for(
            run_pipeline_phase1(
                video_path=episode.video_path,
                target_srt_path=target_srt_path,
                language=tv.target_language, variant=tv.variant,
                target_version_id=target_version_id, provider=provider,
                cached_korean_segments=cached_segments,
                cached_video_proxy_path=episode.video_proxy_path,
                english_srt_path=episode.english_srt_path,
                suggested_not_applicable_lemmas=suggested_not_applicable_lemmas,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

        needs_confirmation = registers_need_confirmation(phase1.get("segment_resolutions", []))

        async with async_session() as session:
            await save_phase1_result(session, target_version_id, phase1)
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "awaiting_confirmation" if needs_confirmation else "review"
            tv.video_proxy_path = phase1.get("video_proxy_path")
            tv.video_offset_seconds = phase1.get("video_offset_seconds") or None
            tv.warnings = phase1.get("warnings") or None
            episode_row = await session.get(Episode, tv.episode_id)
            # 캐시가 아예 없거나, 있어도 옛 형태(granularity 불일치/누락)면
            # 새로 채운다 — 유효한 같은 형태 캐시가 이미 있으면(다른
            # target_version이 먼저 채웠거나 재시도인 경우) 덮어쓰지 않는다.
            if (episode_row.stt_cache is None
                    or episode_row.stt_cache.get("granularity") != STT_CACHE_GRANULARITY):
                episode_row.stt_cache = {
                    "segments": phase1.get("korean_segments_raw", []),
                    "granularity": STT_CACHE_GRANULARITY,
                }
                episode_row.video_proxy_path = phase1.get("video_proxy_path")
            await session.commit()

        # 원본 영상은 S1이 끝난 시점에 지운다 — 이후(사람 확인 대기, S2)는
        # 원본 영상을 전혀 쓰지 않는다(오디오/영상 프록시는 이미 S1에서
        # 만들어 영속화했다). video_proxy_path가 DB에 영속화되기 전에
        # 원본부터 지워버리면, 그 사이 어딘가(STT/프록시 생성 실패 등)에서
        # 죽었을 때 원본도 없고 프록시 경로도 저장되지 않아 생성된 프록시
        # 파일이 고아로 남고, /run-analysis 재시도도 영영 실패하게 된다
        # (필요한 원본 영상이 이미 없으므로).
        #
        # 이 삭제 자체는 별도로 감싼다 — 이미 상태가 커밋까지 끝난 뒤라,
        # 여기서 예외(예: 권한 문제로 unlink가 FileNotFoundError 아닌
        # OSError를 던지는 경우)가 바깥 except로 새어나가 _mark_failed를
        # 호출하면 이미 성공한 S1 결과를 "실패"로 덮어써버리게 된다.
        try:
            delete_original_video(episode.video_path)
        except Exception:
            logger.exception(
                "원본 영상 삭제 실패, S1 결과는 이미 저장됨 (target_version_id=%s)",
                target_version_id,
            )

        if not needs_confirmation:
            await _run_phase2_and_save(target_version_id, provider)
    except asyncio.TimeoutError:
        logger.warning("analyze_and_save 타임아웃 (target_version_id=%s)", target_version_id)
        await _mark_failed(target_version_id, "분석 시간 초과 (1시간)")
    except Exception as exc:
        logger.exception("analyze_and_save 실패 (target_version_id=%s)", target_version_id)
        await _mark_failed(target_version_id, str(exc))


async def _run_phase2_and_save(target_version_id: str, provider: ModelProvider) -> None:
    """S2(Claude/GPT 이중 독립 검증) + S4(최종 안전망)를 실행하고 저장한다.
    analyze_and_save가 확인이 필요 없다고 판단했을 때 곧바로 이어 부르거나,
    POST /target-versions/{id}/confirm-registers가 사람 확인이 끝난 뒤에
    새 백그라운드 태스크로 부른다 — 두 경로 모두 이 함수 하나로 수렴한다."""
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            segments = (await session.execute(
                select(Segment).where(Segment.target_version_id == target_version_id)
                .order_by(Segment.index)
            )).scalars().all()

        profile = load_profile(tv.target_language, tv.variant)
        knowledge = load_knowledge()
        # pending_sensitive_hits는 target_text(이미 사전처리 완료)에 대해
        # 순수 함수로 재계산 가능하다 — S1과 별도 프로세스/태스크에서 재개될
        # 수 있으므로(사람 확인은 임의로 오래 걸릴 수 있음) DB에 따로 저장해
        # 넘기지 않고 여기서 다시 계산한다.
        pairs = pairs_from_segments(segments, target_version_id)
        pending_sensitive_hits = find_pending_sensitive_hits(
            pairs, load_sensitive_terms(), load_profanity_dictionary())
        resolved_registers = resolved_registers_from_segments(segments, target_version_id)

        phase2 = await asyncio.wait_for(
            run_pipeline_phase2(
                pairs, provider, profile, knowledge, pending_sensitive_hits,
                target_version_id, resolved_registers,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

        async with async_session() as session:
            await save_phase2_result(session, target_version_id, phase2)
            tv = await session.get(TargetVersion, target_version_id)
            tv.status = "review"
            tv.warnings = (tv.warnings or []) + (phase2.get("warnings") or []) or None
            await session.commit()
    except asyncio.TimeoutError:
        logger.warning("_run_phase2_and_save 타임아웃 (target_version_id=%s)", target_version_id)
        await _mark_failed(target_version_id, "검증 시간 초과 (1시간)")
    except Exception as exc:
        logger.exception("_run_phase2_and_save 실패 (target_version_id=%s)", target_version_id)
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
