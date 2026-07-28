from datetime import datetime

import pytest
from sqlalchemy import select
from app.db import async_session, engine
from app.models import (
    Base, Title, Episode, TargetVersion, Segment, Character, Relationship,
)
from app.repositories import save_pipeline_result, get_findings
from app.schemas import Finding, AlignedPair, SegmentText, FormatViolation


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_findings():
    async with async_session() as session:
        # NOTE: Title.created_at defaults to a tz-aware datetime (Task 13's
        # models.py), but the DB column is TIMESTAMP WITHOUT TIME ZONE — asyncpg
        # rejects tz-aware values for that column type. Passing a naive value
        # explicitly here sidesteps a pre-existing bug unrelated to this task;
        # see task-14-report.md for details.
        title = Title(name="Test Movie", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()

        result = {
            "findings": [Finding(
                id="f1", target_version_id=tv.id, segment_id="p1",
                category="translation", description="근거", original_text="a",
                suggested_text="b", confidence=0.9, source="llm",
            )],
            "format_violations": [], "characters": [], "relationships": [],
            "gender_questions": [], "register_questions": [],
            "pairs": [AlignedPair(
                id="p1",
                korean=SegmentText(start=0.0, end=1.5, text="한국어"),
                target=SegmentText(start=0.0, end=1.5, text="target text"),
            )],
        }
        await save_pipeline_result(session, tv.id, result)
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 1
        assert rows[0].category == "translation"

        seg_rows = await session.execute(select(Segment).where(Segment.target_version_id == tv.id))
        segments = list(seg_rows.scalars().all())
        assert len(segments) == 1
        # 영속화된 segment id는 target_version_id로 네임스페이싱된다.
        assert segments[0].id == f"{tv.id}:p1"
        assert segments[0].korean_text == "한국어"
        assert segments[0].target_text == "target text"
        # findings.segment_id도 같은 네임스페이싱된 id를 가리켜야 FK가 성립한다.
        assert rows[0].segment_id == f"{tv.id}:p1"
        assert rows[0].id == f"{tv.id}:f1"


def _pipeline_result(target_version_id: str) -> dict:
    """alignment.align()이 실행마다 재사용하는 로컬 ID("pair_1")를 그대로 쓴
    파이프라인 결과. 두 target_version에서 동일한 로컬 ID가 나온다."""
    return {
        "findings": [Finding(
            id="finding_pair_1_translation", target_version_id=target_version_id,
            segment_id="pair_1", category="translation", description="근거",
            original_text="a", suggested_text="b", confidence=0.9, source="llm",
        )],
        "format_violations": [], "characters": [], "relationships": [],
        "gender_questions": [], "register_questions": [],
        "pairs": [AlignedPair(
            id="pair_1",
            korean=SegmentText(start=0.0, end=1.5, text="한국어"),
            target=SegmentText(start=0.0, end=1.5, text="target text"),
        )],
    }


@pytest.mark.asyncio
async def test_two_target_versions_can_be_saved_without_pk_collision():
    """회귀 테스트: alignment의 pair.id는 실행마다 "pair_1"부터 다시 시작하므로,
    서로 다른 target_version의 결과를 같은 DB에 저장하면 segments/findings의
    전역 PK가 충돌했다 (IntegrityError: duplicate key ... "segments_pkey")."""
    async with async_session() as session:
        title_a = Title(name="Movie A", type="movie", created_at=datetime.now())
        title_b = Title(name="Movie B", type="movie", created_at=datetime.now())
        session.add_all([title_a, title_b])
        await session.flush()
        ep_a = Episode(title_id=title_a.id, video_path="/a.mp4")
        ep_b = Episode(title_id=title_b.id, video_path="/b.mp4")
        session.add_all([ep_a, ep_b])
        await session.flush()
        tv_a = TargetVersion(episode_id=ep_a.id, target_language="es", variant="LATAM")
        tv_b = TargetVersion(episode_id=ep_b.id, target_language="es", variant="LATAM")
        session.add_all([tv_a, tv_b])
        await session.flush()

        await save_pipeline_result(session, tv_a.id, _pipeline_result(tv_a.id))
        await session.commit()
        # 두 번째 저장이 첫 번째와 충돌하지 않아야 한다.
        await save_pipeline_result(session, tv_b.id, _pipeline_result(tv_b.id))
        await session.commit()

        all_segments = list((await session.execute(select(Segment))).scalars().all())
        assert len(all_segments) == 2
        assert {s.id for s in all_segments} == {f"{tv_a.id}:pair_1", f"{tv_b.id}:pair_1"}

        rows_a = await get_findings(session, tv_a.id)
        rows_b = await get_findings(session, tv_b.id)
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].segment_id == f"{tv_a.id}:pair_1"
        assert rows_b[0].segment_id == f"{tv_b.id}:pair_1"


async def _make_target_version(session, title: Title) -> TargetVersion:
    episode = Episode(title_id=title.id, video_path="/x.mp4")
    session.add(episode)
    await session.flush()
    tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
    session.add(tv)
    await session.flush()
    return tv


def _result_with(**overrides) -> dict:
    base = {
        "findings": [], "format_violations": [], "characters": [],
        "relationships": [], "gender_questions": [], "register_questions": [],
        "pairs": [AlignedPair(
            id="pair_1",
            korean=SegmentText(start=0.0, end=1.5, text="한국어"),
            target=SegmentText(start=0.0, end=1.5, text="texto....."),
        )],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_characters_deduped_per_title():
    """인물은 title 단위로 공유되므로(design §6), 같은 작품의 다른 화를 분석해도
    같은 label의 Character가 중복 생성되면 안 된다."""
    async with async_session() as session:
        title = Title(name="Series A", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv1 = await _make_target_version(session, title)  # 1화
        tv2 = await _make_target_version(session, title)  # 2화

        chars = [{"label": "민수", "gendered_segment_ids": ["pair_1"]},
                 {"label": "지현", "gendered_segment_ids": []}]
        await save_pipeline_result(session, tv1.id, _result_with(characters=chars))
        await session.commit()
        await save_pipeline_result(session, tv2.id, _result_with(characters=chars))
        await session.commit()

        rows = list((await session.execute(
            select(Character).where(Character.title_id == title.id)
        )).scalars().all())
        assert len(rows) == 2
        assert {c.label for c in rows} == {"민수", "지현"}
        # 확인 대기 신호는 confirmed_gender IS NULL 그 자체다 (별도 저장 불필요).
        assert all(c.confirmed_gender is None for c in rows)


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_relationships_deduped_per_title():
    async with async_session() as session:
        title = Title(name="Series B", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv1 = await _make_target_version(session, title)
        tv2 = await _make_target_version(session, title)

        payload = {
            "characters": [{"label": "민수"}, {"label": "지현"}],
            "relationships": [{"speaker_label": "민수", "addressee_label": "지현",
                               "formality_segment_ids": ["pair_1"]}],
        }
        await save_pipeline_result(session, tv1.id, _result_with(**payload))
        await session.commit()
        await save_pipeline_result(session, tv2.id, _result_with(**payload))
        await session.commit()

        rels = list((await session.execute(
            select(Relationship).where(Relationship.title_id == title.id)
        )).scalars().all())
        assert len(rels) == 1
        speaker = await session.get(Character, rels[0].speaker_character_id)
        addressee = await session.get(Character, rels[0].addressee_character_id)
        assert speaker.label == "민수"
        assert addressee.label == "지현"
        assert rels[0].confirmed_formality_level is None


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_format_violations_as_findings():
    async with async_session() as session:
        title = Title(name="Movie C", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)

        violations = [
            FormatViolation(segment_id="pair_1", rule="ellipsis",
                            detail="연속 온점 4개 이상 감지",
                            auto_fixed=True, fixed_text="texto..."),
            FormatViolation(segment_id="pair_1", rule="line_length",
                            detail="1줄, 최대 줄 길이 60자"),
        ]
        await save_pipeline_result(session, tv.id, _result_with(format_violations=violations))
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 2
        assert all(r.category == "formatting" for r in rows)
        assert all(r.source == "rule" for r in rows)
        # 같은 세그먼트에 두 규칙이 동시에 걸려도 PK가 충돌하지 않는다.
        assert len({r.id for r in rows}) == 2
        # FK는 Fix 1의 네임스페이싱된 segment id를 가리켜야 한다.
        assert all(r.segment_id == f"{tv.id}:pair_1" for r in rows)
        by_rule = {r.description: r for r in rows}
        ellipsis_row = by_rule["연속 온점 4개 이상 감지"]
        line_length_row = by_rule["1줄, 최대 줄 길이 60자"]

        # 자동보정된 온점 위반은 이미 텍스트에 적용된 기계적 규칙이라 검수자가
        # 결정할 것이 없다 → 바로 approved로 확정된다.
        assert ellipsis_row.suggested_text == "texto..."
        assert ellipsis_row.status == "approved"
        assert ellipsis_row.final_text == "texto..."

        # 줄 길이 위반은 의미 보존이 필요한 판단이라 검수자에게 남긴다.
        assert line_length_row.suggested_text == ""
        assert line_length_row.original_text == "texto....."
        assert line_length_row.status == "pending"
        assert line_length_row.final_text == ""
