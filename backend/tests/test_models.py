from sqlalchemy import inspect
from app.db import engine
from app.models import Base
import asyncio
import pytest


@pytest.mark.asyncio
async def test_all_expected_tables_exist():
    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    expected = {
        "titles", "episodes", "characters", "relationships", "target_versions",
        "segments", "findings", "stt_corrections", "learned_examples", "exports",
    }
    assert expected.issubset(set(tables))
