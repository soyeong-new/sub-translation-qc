import pytest
from app.db import engine


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _run_analysis_inline(monkeypatch):
    """실제 Redis 없이도 /run-analysis가 즉시 실행되도록, 큐 등록을
    run_analysis_job 직접 호출로 대체한다. arq_redis 인자는 무시되므로
    앱 lifespan(실제 Redis 연결)이 테스트에서 실행되지 않아도 된다.

    테스트는 httpx.ASGITransport로 앱을 호출하는데, 이는 FastAPI의 lifespan을
    실행하지 않는다. 그래서 request.app.state.arq_redis가 애초에 설정되지
    않아 속성 접근 자체가 AttributeError를 낸다 — 아래 enqueue_analysis
    몽키패치와 무관하게 먼저 터진다. 값 자체는 곧바로 무시되는 인자이므로
    더미 값으로 미리 채워 둔다."""
    from app.main import app
    from app.worker import run_analysis_job

    monkeypatch.setattr(app.state, "arq_redis", None, raising=False)

    async def _inline_enqueue(redis_pool, target_version_id, target_srt_path):
        await run_analysis_job({}, target_version_id, target_srt_path)

    monkeypatch.setattr("app.main.enqueue_analysis", _inline_enqueue)
    yield
