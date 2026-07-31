import asyncio
from datetime import datetime
from unittest.mock import patch
import pytest
from app.db import engine, async_session
from app.models import Base, Title, Episode, TargetVersion
from app.worker import run_analysis_job, WorkerSettings

TARGET_SRT = """1
00:00:00,000 --> 00:00:02,000
hola
"""


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_target_version(status="analyzing") -> str:
    async with async_session() as session:
        title = Title(name="T", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                           status=status)
        session.add(tv)
        await session.commit()
        return tv.id


@pytest.mark.asyncio
async def test_job_sets_status_review_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"):
        await run_analysis_job({}, tv_id, str(srt_path))

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "review"
        assert tv.error_message is None


@pytest.mark.asyncio
async def test_job_sets_status_failed_on_exception(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    with patch("app.worker.run_pipeline", side_effect=RuntimeError("STT 실패")):
        await run_analysis_job({}, tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message == "STT 실패"


@pytest.mark.asyncio
async def test_job_does_not_raise_when_target_version_missing(monkeypatch):
    """target_version_id가 존재하지 않으면(예: 그 사이 삭제됨) 첫 조회에서 tv가
    None이 되고, 뒤이은 tv.episode_id 접근에서 AttributeError가 발생해 바깥
    except로 넘어간다. 이때 실패 상태를 기록하려고 except 블록 안에서 다시
    session.get(TargetVersion, ...)을 호출해도 여전히 None이므로, 가드 없이
    tv.status = "failed"를 실행하면 두 번째 AttributeError가 run_analysis_job
    밖으로 새어나가 "예외를 재발생시키지 않는다"는 계약을 깬다. 이 테스트는
    그 이중 실패 상황에서도 run_analysis_job이 조용히(예외 없이) 끝나는지
    확인한다 — await 자체가 예외를 던지면 테스트가 실패한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    await run_analysis_job({}, "does-not-exist", "/nonexistent.srt")


@pytest.mark.asyncio
async def test_job_does_not_raise_when_target_version_deleted_mid_flight(monkeypatch):
    """첫 조회 시점에는 target_version이 존재해 파이프라인 실행까지 진행되지만,
    (예: 다른 요청이 동시에 지운 경우) 실패 상태를 기록하는 except 블록의
    두 번째 session.get 호출 시점에는 이미 사라져 None이 돌아오는 경우를
    모사한다. 첫 번째 TargetVersion 조회는 실제 값을 돌려주게 하고, 두 번째
    (except 블록의) 조회부터만 None을 돌려주도록 해 "존재하다가 중간에 삭제된"
    상황을 정확히 재현한다. 이 경우에도 예외가 새어나가지 않아야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models import TargetVersion as TV

    real_get = AsyncSession.get
    call_count = {"target_version_gets": 0}

    async def get_none_from_second_call(self, entity, ident, *args, **kwargs):
        if entity is TV:
            call_count["target_version_gets"] += 1
            if call_count["target_version_gets"] == 1:
                return await real_get(self, entity, ident, *args, **kwargs)
            return None
        return await real_get(self, entity, ident, *args, **kwargs)

    with patch("app.worker.run_pipeline", side_effect=RuntimeError("STT 실패")), \
         patch.object(AsyncSession, "get", get_none_from_second_call):
        await run_analysis_job({}, tv_id, "/nonexistent.srt")


@pytest.mark.asyncio
async def test_job_writes_failed_status_and_reraises_on_cancellation(monkeypatch):
    """arq는 job_timeout을 넘긴 작업을 asyncio.CancelledError로 취소한다.
    CancelledError는 BaseException을 상속하므로 일반 `except Exception`으로는
    잡히지 않는다 — run_analysis_job이 이를 처리하지 않으면 실패 상태 기록이
    전혀 실행되지 않고 target_version.status가 "analyzing"에 영원히 멈춘 채,
    arq 재시도가 전체 파이프라인을 조용히 재실행해 비용만 반복 청구한다. 이
    테스트는 CancelledError가 발생해도 실패 상태가 기록된 뒤, 취소 자체는
    (asyncio/arq의 정상적인 태스크 정리를 위해) 다시 raise되는지 확인한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    tv_id = await _make_target_version()

    with patch("app.worker.run_pipeline", side_effect=asyncio.CancelledError()):
        with pytest.raises(asyncio.CancelledError):
            await run_analysis_job({}, tv_id, "/nonexistent.srt")

    async with async_session() as session:
        tv = await session.get(TargetVersion, tv_id)
        assert tv.status == "failed"
        assert tv.error_message == "분석 시간 초과 또는 취소됨"


def test_worker_settings_function_name_matches_enqueue_string():
    """enqueue_analysis는 문자열 "run_analysis_job"으로 arq에 작업을 등록한다
    (app.worker.enqueue_analysis 참고). 이 문자열은 arq가 WorkerSettings.functions에
    등록된 함수를 찾을 때 쓰는 이름(__qualname__)과 반드시 일치해야 한다.
    테스트 스위트의 autouse fixture가 enqueue_analysis 자체를 갈아치우기 때문에,
    이 문자열이 실제로 맞는지는 지금까지 아무 테스트도 검증하지 않았다 — 함수
    이름이 나중에 바뀌면 프로덕션에서 조용히 깨지고 스위트는 계속 초록불일
    위험을 막는 값싼 가드."""
    qualnames = {f.__qualname__ for f in WorkerSettings.functions}
    assert "run_analysis_job" in qualnames
