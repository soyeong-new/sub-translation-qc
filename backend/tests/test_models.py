from sqlalchemy import inspect
from app.db import engine
from app.models import Base
import asyncio
import pytest


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
