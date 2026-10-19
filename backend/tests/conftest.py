import asyncio
import pytest
from app.db import engine


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _run_analysis_inline(monkeypatch):
    """asyncio.create_task를 기존 동작 대로 실행하되, background_tasks set을 초기화한다.

    테스트는 httpx.ASGITransport로 앱을 호출하는데, 이는 FastAPI의 lifespan을
    실행하지 않는다. 그래서 request.app.state.background_tasks가 애초에 설정되지
    않아 속성 접근 자체가 AttributeError를 낸다 — 미리 initialized set으로
    채워 둔다.

    주의: 이 fixture는 이제 단순히 background_tasks를 초기화할 뿐, task 실행을
    변경하지 않는다. 각 테스트는 background task 완료를 직접 관리해야 한다."""
    from app.main import app

    # 테스트 중 background_tasks set 초기화
    monkeypatch.setattr(app.state, "background_tasks", set(), raising=False)
    yield


@pytest.fixture(autouse=True)
def _disable_qc_trace_file(monkeypatch):
    """개발용 반영 기록(qc_trace)이 테스트 중 실제 media 폴더에 파일을 쓰지 않게 끈다."""
    monkeypatch.setattr("app.core.pipeline.TRACE_DIR", None)


@pytest.fixture(autouse=True)
def _disable_llm_trace_file(monkeypatch):
    """모델 입출력 원문 기록(llm_trace)이 테스트 중 실제 media 폴더에 파일을 쓰지 않게 끈다."""
    monkeypatch.setattr("app.core.llm_trace.TRACE_DIR", None)
