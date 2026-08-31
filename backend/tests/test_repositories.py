from datetime import datetime

import pytest
from sqlalchemy import select
from app.db import async_session, engine
from app.models import Base, Title, Episode, TargetVersion, Segment, FindingRow, SttCorrection
from app.repositories import (
    save_pipeline_result, get_findings, delete_target_version_results,
    get_character_gender_facts, upsert_character_gender_facts,
)
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
                category="mistranslation", description="근거", original_text="a",
                suggested_text="b", confidence=0.9, source="llm",
            )],
            "format_violations": [],
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
        assert rows[0].category == "mistranslation"

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
            segment_id="pair_1", category="mistranslation", description="근거",
            original_text="a", suggested_text="b", confidence=0.9, source="llm",
        )],
        "format_violations": [],
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
        "findings": [], "format_violations": [],
        "pairs": [AlignedPair(
            id="pair_1",
            korean=SegmentText(start=0.0, end=1.5, text="한국어"),
            target=SegmentText(start=0.0, end=1.5, text="texto....."),
        )],
    }
    base.update(overrides)
    return base


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


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_same_segment_ellipsis_violation_twice():
    """회귀 테스트(critical): 같은 세그먼트가 온점 위반으로 두 번 걸리면(최초
    체크에서 한 번, GPT 2차 이후 최종 재체크에서 또 한 번 — pipeline.py가 GPT의
    재작성이 새 온점을 만들 수 있어 재검사한다) FormatViolation 두 개가 같은
    (segment_id, rule) 조합을 갖는다. 예전 구현은 id를
    f"finding_{segment_id}_formatting_{rule}"로만 만들어 두 번째 저장 시
    findings_pkey UNIQUE 제약을 위반했고, save_pipeline_result가 던진
    IntegrityError를 background.analyze_and_save가 잡아 전체 target_version을
    failed로 처리했다 — STT + Claude/GPT 두 패스 비용이 전부 날아가는 버그였다.
    이 테스트는 save_pipeline_result가 예외 없이 두 finding을 모두(서로 다른
    PK로) 저장하는지, 그리고 각 finding의 original_text가 파이프라인 최종
    상태 하나로 뭉개지지 않고 그 체크포인트 고유의 "고치기 전" 텍스트를
    유지하는지 검증한다(후속 리뷰에서 발견된 important 버그: original_text를
    result["pairs"]의 최종 상태로 되짚어 재구성하면 두 finding이 서로 다른
    시점에 감지됐는데도 동일한 — 그리고 대부분 틀린 — 값을 갖게 된다)."""
    async with async_session() as session:
        title = Title(name="Movie D", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)

        violations = [
            FormatViolation(segment_id="pair_1", rule="ellipsis",
                            detail="연속 온점 4개 이상 감지 (최초)",
                            auto_fixed=True, fixed_text="texto...",
                            original_text="BAD_TRANSLATION texto...."),
            FormatViolation(segment_id="pair_1", rule="ellipsis",
                            detail="연속 온점 4개 이상 감지 (GPT 이후 재검사)",
                            auto_fixed=True, fixed_text="espera...",
                            original_text="espera......"),
        ]
        # IntegrityError 없이 커밋까지 끝나야 한다 — 예외가 나면 이 테스트가
        # 실패한다. _result_with의 기본 pairs target text("texto.....")는 두
        # violation의 original_text와도 다르게 둬서, repositories.py가 파이프라인
        # 최종 상태로 되짚어 재구성하는 게 아니라 각 FormatViolation.original_text를
        # 그대로 쓴다는 걸 구분해서 검증할 수 있게 한다.
        await save_pipeline_result(session, tv.id, _result_with(format_violations=violations))
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 2
        assert len({r.id for r in rows}) == 2  # PK 충돌 없이 둘 다 저장됨
        assert all(r.category == "formatting" and r.status == "approved" for r in rows)
        by_description = {r.description: r for r in rows}
        first = by_description["연속 온점 4개 이상 감지 (최초)"]
        second = by_description["연속 온점 4개 이상 감지 (GPT 이후 재검사)"]
        assert first.suggested_text == "texto..."
        assert second.suggested_text == "espera..."
        # 회귀(important): 각 체크포인트 고유의 original_text가 유지된다 — 둘
        # 다 파이프라인 최종 pairs 상태("texto.....")로 뭉개지지 않는다.
        assert first.original_text == "BAD_TRANSLATION texto...."
        assert second.original_text == "espera......"
        assert first.original_text != second.original_text
        assert first.original_text != "texto....."
        assert second.original_text != "texto....."


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_finding_model():
    async with async_session() as session:
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
                category="mistranslation", description="근거", original_text="a",
                suggested_text="b", confidence=0.9, source="llm", model="claude",
            )],
            "format_violations": [],
            "pairs": [AlignedPair(
                id="p1",
                korean=SegmentText(start=0.0, end=1.5, text="한국어"),
                target=SegmentText(start=0.0, end=1.5, text="target text"),
            )],
        }
        await save_pipeline_result(session, tv.id, result)
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert rows[0].model == "claude"


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_final_text_and_status_for_pretreatment_findings():
    """회귀(important): FindingRow(...) 생성에서 final_text/reviewer_name을
    빠뜨리면, pretreatment.py/safety_net.py가 status="approved",
    final_text=suggested_text로 직접 구성해 넘긴 Finding이 DB에는
    final_text=""(기본값)로 저장된다 — 검수자 판단 없이 이미 확정된 자동교정
    결과가 검수 화면에서 빈 텍스트로 보이는 버그였다. 이 회귀는 pretreatment/
    safety_net을 파이프라인 테스트에서 직접 거치지 않으면(글로서리/CTA/비속어
    사전이 비어 있으면 둘 다 findings=[]를 반환) 드러나지 않으므로, 여기서
    run_pretreatment를 실제 글로서리 항목으로 실행해 진짜 Finding을 만들고
    save_pipeline_result에 그대로 흘려보내 영속화된 행을 검증한다."""
    from app.core.pretreatment import run_pretreatment
    from app.schemas import AlignedPair, SegmentText

    async with async_session() as session:
        title = Title(name="Movie E", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)

        pairs = [AlignedPair(id="pair_1",
                              target=SegmentText(start=0.0, end=1.5, text="Cholsu가 왔다"))]
        glossary = [{"canonical": "Chulsoo", "aliases": ["Cholsu"]}]
        pretreatment = run_pretreatment(pairs, glossary, [], [], [], tv.id)
        assert pretreatment.findings, "글로서리 항목이 실제로 Finding을 만들어야 이 테스트가 유효하다"

        result = _result_with(findings=pretreatment.findings, pairs=pretreatment.pairs)
        await save_pipeline_result(session, tv.id, result)
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.category == "glossary"
        assert row.status == "approved"
        assert row.suggested_text == "Chulsoo가 왔다"
        # 이 assertion이 회귀의 핵심이다: final_text가 누락되면 ""로 저장된다.
        assert row.final_text == "Chulsoo가 왔다"


@pytest.mark.asyncio
async def test_delete_target_version_results_removes_segments_and_findings():
    async with async_session() as session:
        title = Title(name="T", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                      korean_text="안녕", target_text="hola")
        session.add(seg)
        await session.flush()
        finding = FindingRow(
            target_version_id=tv.id, segment_id=seg.id, category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0,
        )
        session.add(finding)
        await session.commit()
        tv_id = tv.id

    async with async_session() as session:
        await delete_target_version_results(session, tv_id)
        await session.commit()

    async with async_session() as session:
        remaining_segments = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        remaining_findings = (await session.execute(
            select(FindingRow).where(FindingRow.target_version_id == tv_id)
        )).scalars().all()

    assert remaining_segments == []
    assert remaining_findings == []


@pytest.mark.asyncio
async def test_delete_target_version_results_removes_stt_corrections_too():
    """회귀 테스트: stt_corrections.segment_id도 segments.id를 참조하는 하드
    FK(ondelete 없음)다. POST /segments/{id}/correct-stt는 분석 상태와 무관하게
    SttCorrection을 만들 수 있으므로, 검수자가 STT 텍스트를 교정한 뒤 같은
    target_version에 run-analysis를 재시도하면 delete_target_version_results가
    SttCorrection을 먼저 지우지 않는 한 Segment 삭제가 IntegrityError로
    실패한다 — findings와 동일한 종류의 버그가 다른 테이블을 통해 재현되는
    경우다."""
    async with async_session() as session:
        title = Title(name="T2", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                      korean_text="안녕", target_text="hola")
        session.add(seg)
        await session.flush()
        correction = SttCorrection(
            segment_id=seg.id, original_text="안뇽", corrected_text="안녕",
            reviewer_name="reviewer",
        )
        session.add(correction)
        await session.commit()
        tv_id, correction_id = tv.id, correction.id

    async with async_session() as session:
        # IntegrityError 없이 커밋까지 끝나야 한다 — 예외가 나면 이 테스트가
        # 실패한다.
        await delete_target_version_results(session, tv_id)
        await session.commit()

    async with async_session() as session:
        remaining_segments = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all()
        surviving_correction = await session.get(SttCorrection, correction_id)

    assert remaining_segments == []
    assert surviving_correction is None


@pytest.mark.asyncio
async def test_save_pipeline_result_persists_segment_resolution_flags():
    async with async_session() as session:
        title = Title(name="T", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()

        result = {
            "pairs": [
                AlignedPair(id="p1", target=SegmentText(start=0, end=1, text="Esta cansada.")),
                AlignedPair(id="p2", target=SegmentText(start=1, end=2, text="Que tal.")),
            ],
            "findings": [], "format_violations": [],
            "segment_resolutions": [
                {"segment_id": "p1", "gender_check_needed": True, "formality_check_needed": False},
                {"segment_id": "p2", "gender_check_needed": False, "formality_check_needed": True},
            ],
        }
        await save_pipeline_result(session, tv.id, result)
        await session.commit()
        tv_id = tv.id

    async with async_session() as session:
        segs = (await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id).order_by(Segment.index)
        )).scalars().all()
        seg1, seg2 = segs
        assert seg1.gender_check_needed is True
        assert seg1.formality_check_needed is False
        assert seg2.gender_check_needed is False
        assert seg2.formality_check_needed is True


@pytest.mark.asyncio
async def test_get_findings_order_stays_stable_after_update():
    """회귀: ORDER BY 없이는 UPDATE(승인/거부/수정/재질문) 후 SELECT 순서가
    바뀔 수 있어, 검수자가 방금 수정한 카드가 목록에서 다른 자리로 옮겨가
    "없어진 것처럼" 보이는 문제가 있었다 — 순서가 항상 고정돼야 한다."""
    async with async_session() as session:
        title = Title(name="Movie Order", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)
        session.add(Segment(id="seg_b", target_version_id=tv.id, index=0, start=0.0, end=1.0))
        session.add(Segment(id="seg_a", target_version_id=tv.id, index=1, start=1.0, end=2.0))
        await session.flush()
        session.add(FindingRow(
            id="f_b", target_version_id=tv.id, segment_id="seg_b", category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0))
        session.add(FindingRow(
            id="f_a", target_version_id=tv.id, segment_id="seg_a", category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0))
        await session.commit()
        tv_id = tv.id

    async with async_session() as session:
        before = [f.id for f in await get_findings(session, tv_id)]

    async with async_session() as session:
        finding = await session.get(FindingRow, "f_b")
        finding.status = "modified"
        finding.final_text = "수정됨"
        await session.commit()

    async with async_session() as session:
        after = [f.id for f in await get_findings(session, tv_id)]

    assert before == after


@pytest.mark.asyncio
async def test_get_findings_orders_by_video_position_not_segment_id_string():
    """회귀: segment_id 문자열로 정렬하면 "pair_10"이 "pair_2"보다 앞에 오는
    등 실제 영상 순서와 어긋난다 — Segment.index(영상 안에서의 진짜 순번)로
    정렬해야 한다."""
    async with async_session() as session:
        title = Title(name="Movie Order2", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)
        # segment_id 문자열로 정렬하면 pair_10이 pair_2보다 앞에 오지만,
        # 영상 순서(index)로는 pair_2(index=1)가 pair_10(index=9)보다 앞이다.
        session.add(Segment(id="pair_10", target_version_id=tv.id, index=9, start=90.0, end=91.0))
        session.add(Segment(id="pair_2", target_version_id=tv.id, index=1, start=10.0, end=11.0))
        await session.flush()
        session.add(FindingRow(
            id="f_10", target_version_id=tv.id, segment_id="pair_10", category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0))
        session.add(FindingRow(
            id="f_2", target_version_id=tv.id, segment_id="pair_2", category="mistranslation",
            description="d", original_text="a", suggested_text="b", confidence=1.0))
        await session.commit()
        tv_id = tv.id

    async with async_session() as session:
        ordered = [f.id for f in await get_findings(session, tv_id)]
    assert ordered == ["f_2", "f_10"]


@pytest.mark.asyncio
async def test_upsert_and_get_character_gender_facts():
    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()

        await upsert_character_gender_facts(session, title.id, {"성경": "female"})
        await session.commit()

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title.id)
        assert facts == {"성경": "female"}


@pytest.mark.asyncio
async def test_upsert_character_gender_facts_overwrites_existing_value():
    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        await upsert_character_gender_facts(session, title.id, {"성경": "female"})
        await session.commit()

    async with async_session() as session:
        # 검수자가 이전 값과 다르게 정정하면 새 값으로 덮어써야 한다 —
        # 사람의 판단이 항상 최종 권위를 가진다(design 원칙 4).
        await upsert_character_gender_facts(session, title.id, {"성경": "male"})
        await session.commit()

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title.id)
        assert facts == {"성경": "male"}


@pytest.mark.asyncio
async def test_get_character_gender_facts_lookup_is_case_insensitive():
    """회귀(design 논의): 언어마다 독립적으로 도는 LLM이 같은 인물 이름을
    대소문자만 다르게 뽑는 사례가 실측으로 확인됐다("Bo-Na" vs "Bo-na") —
    조회 키를 casefold해서 대소문자 차이로 재사용이 조용히 실패하지 않게
    한다."""
    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        await upsert_character_gender_facts(session, title.id, {"Bo-Na": "female"})
        await session.commit()

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title.id)
        assert facts.get("bo-na") == "female"


@pytest.mark.asyncio
async def test_upsert_character_gender_facts_matches_existing_name_case_insensitively():
    """회귀: 다른 언어 실행이 이름을 대소문자만 다르게 뽑아도 새 row를
    또 만들지 않고 기존 값을 갱신해야 한다 — 안 그러면 같은 인물의 기록이
    여러 개로 흩어진다."""
    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        await upsert_character_gender_facts(session, title.id, {"Bo-Na": "female"})
        await session.commit()

    async with async_session() as session:
        await upsert_character_gender_facts(session, title.id, {"bo-na": "female"})
        await session.commit()

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title.id)
        assert len(facts) == 1


@pytest.mark.asyncio
async def test_upsert_character_gender_facts_matches_existing_name_across_unicode_normalization():
    """회귀(실측: title 이름 중복 사고와 같은 원인 클래스): 같은 한글이라도
    유니코드 정규화 형태가 다르면(NFC 완성형 vs NFD 분리형) 화면엔 똑같이
    보여도 바이트가 달라, casefold만으로는 다른 이름으로 인식돼 row가
    갈라진다. 저장/조회 모두 NFC로 정규화해서 표기 형태가 달라도 같은
    인물로 인식해야 한다."""
    import unicodedata

    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        nfc_name = "은상"
        await upsert_character_gender_facts(session, title.id, {nfc_name: "female"})
        await session.commit()

    async with async_session() as session:
        nfd_name = unicodedata.normalize("NFD", "은상")
        assert nfd_name != nfc_name  # 바이트로는 실제로 다른 문자열임을 확인
        await upsert_character_gender_facts(session, title.id, {nfd_name: "male"})
        await session.commit()

    async with async_session() as session:
        facts = await get_character_gender_facts(session, title.id)
        assert len(facts) == 1  # 같은 인물로 인식돼 하나로 합쳐져야 한다


@pytest.mark.asyncio
async def test_get_character_gender_facts_scoped_to_title():
    async with async_session() as session:
        title_a = Title(name="Drama A", type="series", created_at=datetime.now())
        title_b = Title(name="Drama B", type="series", created_at=datetime.now())
        session.add_all([title_a, title_b])
        await session.flush()
        await upsert_character_gender_facts(session, title_a.id, {"성경": "female"})
        await session.commit()

    async with async_session() as session:
        # 다른 title(작품)의 이름은 절대 섞이면 안 된다 — 이게 이 기능의
        # 핵심 안전장치다(design §과거 GenderWordResolution 제거 사유와의 차이).
        facts_b = await get_character_gender_facts(session, title_b.id)
        assert facts_b == {}
