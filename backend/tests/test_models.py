from sqlalchemy import inspect
from app.db import engine
from app.models import Base
import asyncio
import pytest


@pytest.fixture(autouse=True)
async def _ensure_tables_exist():
    # test_repositories.py 등 create_all/drop_all 패턴을 쓰는 다른 테스트가 같은
    # pytest 세션에서 먼저 실행되면 drop_all로 인해 이 테스트가 의존하는 테이블이
    # 사라질 수 있다. create_all은 멱등이므로(이미 있으면 no-op) 매번 선실행해서
    # 이 테스트를 실행 순서에 무관하게 만든다. 사후 drop_all은 하지 않는다 — 이
    # 테스트는 "테이블이 없다"를 검증하지 않고, 뒤에 실행될 테스트를 위해 테이블을
    # 남겨두는 편이 낫다.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def _dispose_engine_after():
    yield
    # test_repositories.py도 같은 방식으로 engine을 dispose한다: pytest-asyncio가
    # 테스트마다 새 이벤트 루프를 쓰는데 engine의 asyncpg 커넥션 풀은 임포트 시점에 한
    # 번만 만들어져 특정 루프에 바인딩되므로, dispose하지 않으면 다음 테스트(다른 루프)가
    # 이 풀을 재사용하려다 "attached to a different loop" RuntimeError로 죽는다.
    await engine.dispose()


@pytest.mark.asyncio
async def test_all_expected_tables_exist():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    expected = {
        "titles", "episodes", "characters", "relationships", "target_versions",
        "segments", "findings", "stt_corrections", "learned_examples", "exports",
    }
    assert expected.issubset(set(tables))
