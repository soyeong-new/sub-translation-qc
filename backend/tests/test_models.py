from sqlalchemy import inspect
from app.db import engine
from app.models import Base
import asyncio
import pytest


@pytest.fixture(autouse=True)
async def _ensure_tables_exist():
    # test_repositories.py 등 create_all/drop_all 패턴을 쓰는 다른 테스트가 같은
    # pytest 세션에서 먼저 실행되면 drop_all로 인해 이 테스트가 의존하는 테이블이
    # 사라질 수 있다. drop_all 후 create_all을 매번 실행해서 최신 ORM 정의를 반영하고
    # 이 테스트를 실행 순서에 무관하게 만든다.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
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


@pytest.mark.asyncio
async def test_episodes_table_has_stt_cache_columns():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("episodes")]
        )
    assert "stt_cache" in columns
    assert "video_proxy_path" in columns


@pytest.mark.asyncio
async def test_target_versions_table_has_warnings_column():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("target_versions")]
        )
    assert "warnings" in columns


@pytest.mark.asyncio
async def test_titles_table_has_chart_columns():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("titles")]
        )
    assert "chart_image_path" in columns
    assert "chart_extraction_status" in columns
    assert "chart_extraction_error" in columns


@pytest.mark.asyncio
async def test_characters_table_has_chart_columns():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("characters")]
        )
    assert "suggested_gender" in columns
    assert "source" in columns


@pytest.mark.asyncio
async def test_relationships_table_has_relationship_type_column():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("relationships")]
        )
    assert "relationship_type" in columns


@pytest.mark.asyncio
async def test_segments_table_has_resolution_columns():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("segments")]
        )
    for col in ("gender_check_needed", "formality_check_needed", "resolved_character_id",
                "resolved_gender_raw", "resolved_relationship_id", "resolved_formality_raw"):
        assert col in columns, f"{col} 컬럼이 없음"


@pytest.mark.asyncio
async def test_segments_table_has_anchor_candidates_columns():
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("segments")]
        )
    for col in ("gender_anchor_candidates", "formality_anchor_candidates"):
        assert col in columns, f"{col} 컬럼이 없음"
