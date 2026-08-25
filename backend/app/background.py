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
from app.repositories import (
    save_phase1_result, save_phase2_result, get_character_gender_facts, get_episode_gender_facts,
)
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
STT_CACHE_GRANULARITY = "word_with_korean_cue_index"
# 원본 영상은 첫 분석 성공 직후 삭제된다(재업로드 경로 없음) — 그래서
# granularity 태그가 안 맞는다고 캐시를 무조건 버리면, 이미 분석된
# 에피소드는 재분석/다른 언어 추가 시 STT를 다시 돌릴 소스 자체가 없어서
# 그대로 실패한다. 새로 쓰는 태그는 이 값 그대로 유지하되(새 캐시는 계속
# cue_index를 포함하게), "읽을 때 유효하다고 인정하는" 태그 집합은 옛
# 태그도 같이 받아준다 — cue_index 없는 옛 캐시는 pipeline.py의 기존
# 폴백이 단어 단위 align()으로 안전하게 처리한다(개선은 못 받지만 실패는
# 안 한다).
READABLE_STT_CACHE_GRANULARITIES = ("word", STT_CACHE_GRANULARITY)


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
            # 이 title(작품)에서 이미 확인된 캐릭터 성별 — 같은 작품의
            # 다른 회차/언어판에서 재사용한다(design §시리즈/다국어 간
            # 캐릭터 성별 재사용).
            known_gender_facts = await get_character_gender_facts(session, episode.title_id)
            # 같은 회차의 다른 언어 버전에서 이미 확인된, 이름 없는 인물의
            # 성별도(순번+한국어 원문이 정확히 같을 때만) 재사용한다(design
            # §회차 내 문장 기준 재사용).
            episode_gender_facts = await get_episode_gender_facts(
                session, episode.id, target_version_id)

        provider = get_provider()
        # READABLE_STT_CACHE_GRANULARITIES에 속한 형태(옛 "word" 단위든 새
        # cue_index 포함 단위든)의 캐시는 재사용한다 — 원본 영상이 이미
        # 삭제됐을 수 있어(재업로드 경로 없음) 캐시가 유일한 STT 소스인
        # 경우가 흔하다. "word" 캐시는 cue_index가 없어 pipeline.py가 옛
        # 단어 단위 align()으로 폴백한다(개선은 못 받지만 실패는 안 한다).
        cached_segments = (
            episode.stt_cache.get("segments")
            if episode.stt_cache and episode.stt_cache.get("granularity") in READABLE_STT_CACHE_GRANULARITIES
            else None
        )
        # run_pipeline_phase1이 실제로 캐시를 재사용할지는 이 두 값이 둘 다
        # 있어야 하는 것과 정확히 같은 조건이다(그 함수 내부의 분기와
        # 반드시 일치해야 한다 — 아래 캐시 쓰기 판단이 이 값에 의존한다).
        cache_will_be_reused = cached_segments is not None and episode.video_proxy_path is not None
        phase1 = await asyncio.wait_for(
            run_pipeline_phase1(
                video_path=episode.video_path,
                target_srt_path=target_srt_path,
                language=tv.target_language, variant=tv.variant,
                target_version_id=target_version_id, provider=provider,
                cached_korean_segments=cached_segments,
                cached_video_proxy_path=episode.video_proxy_path,
                korean_srt_path=episode.korean_srt_path,
                known_gender_facts=known_gender_facts,
                episode_gender_facts=episode_gender_facts,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )

        needs_confirmation = registers_need_confirmation(phase1.get("segment_resolutions", []))

        async with async_session() as session:
            await save_phase1_result(session, target_version_id, phase1)
            tv = await session.get(TargetVersion, target_version_id)
            # "review"가 아니라 "verifying"이다 — 이 시점엔 아직 S2(AI 검증,
            # findings을 실제로 만드는 단계)가 시작 전이다. 여기서 곧장
            # "review"로 표시하면 검토 화면이 findings 0개인 채로 "다 됐다"고
            # 뜨는 경쟁 상태가 생긴다(프론트는 이미 "verifying"을 계속
            # 폴링하도록 처리돼 있다 — api.js pollTargetVersionStatus).
            tv.status = "awaiting_confirmation" if needs_confirmation else "verifying"
            tv.video_proxy_path = phase1.get("video_proxy_path")
            tv.video_offset_seconds = phase1.get("video_offset_seconds") or None
            tv.warnings = phase1.get("warnings") or None
            episode_row = await session.get(Episode, tv.episode_id)
            # 이번 실행이 캐시를 재사용하지 않고 STT를 실제로 새로 돌렸을
            # 때만 캐시를 (다시) 쓴다. 회귀(실제 데이터로 재현): 이전 버전은
            # "기존 캐시의 태그가 최신과 다르면 새로 쓴다"는 조건만 봤는데,
            # 캐시를 재사용한 실행에서는 phase1의 korean_segments_raw가
            # 재사용한 옛 데이터(cue_index 없음) 그대로다 — 그런데도 태그는
            # 최신으로 다시 써버려서, 실제로는 여전히 옛 데이터인데 "이미
            # cue_index 있음"으로 영구히 잘못 표시되는 사고가 났다(그 뒤로는
            # 매번 이 잘못 표시된 캐시를 신뢰해 재사용하며 새 정렬 알고리즘을
            # 영영 못 받음). 캐시가 아예 없는 경우(cache_will_be_reused=False)
            # 는 항상 새로 쓴다.
            if not cache_will_be_reused:
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
