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


@pytest.mark.asyncio
async def test_all_expected_tables_exist():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    expected = {
        "titles", "episodes", "characters", "relationships", "target_versions",
        "segments", "findings", "stt_corrections", "learned_examples", "exports",
    }
    assert expected.issubset(set(tables))


@pytest.mark.asyncio
async def test_findings_table_has_model_column():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("findings")]
        )
    assert "model" in columns
