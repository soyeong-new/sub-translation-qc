import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, FindingRow, Title, Episode, TargetVersion, Segment


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _make_finding_row(finding_id: str) -> None:
    # NOTE: Task 13 added FK constraints (findings.target_version_id ->
    # target_versions.id, findings.segment_id -> segments.id) enforced by the
    # real Postgres DB. The brief's literal test used bare "tv1"/"p1" strings,
    # which violate those FKs on insert. Following the precedent set in Task 14
    # (see task-14-report.md), real parent rows are created first so the
    # FindingRow insert succeeds; the finding's own id stays the literal "f1"
    # since the review-action tests target it by path parameter.
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        # end를 넉넉히 줘서(5초) 읽기 속도 제약이 이 테스트들의 실제 관심사
        # (승인/거부/pick 트랜잭션)를 방해하지 않게 한다.
        segment = Segment(id="p1", target_version_id=tv.id, index=0, start=0.0, end=5.0)
        session.add(segment)
        await session.flush()
        f = FindingRow(id=finding_id, target_version_id=tv.id, segment_id="p1",
                       category="mistranslation", description="근거",
                       original_text="a", suggested_text="b", confidence=0.9)
        session.add(f)
        await session.commit()


@pytest.mark.asyncio
async def test_reviewer_can_approve_a_finding():
    await _make_finding_row("f1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reviewer_can_modify_with_final_text():
    await _make_finding_row("f1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "modified", "reviewer_name": "김검수", "final_text": "c"},
        )
        assert r.status_code == 200
        assert r.json()["final_text"] == "c"


async def _make_disagreeing_pair() -> tuple[str, str]:
    """같은 세그먼트에 대해 Claude/GPT가 서로 다르게 제안한 pending finding
    두 개를 만든다 — pick 엔드포인트 테스트용."""
    async with async_session() as session:
        title = Title(name="T", type="movie")
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv)
        await session.flush()
        # end를 넉넉히 줘서(5초) 읽기 속도 제약이 이 테스트들의 실제 관심사
        # (승인/거부/pick 트랜잭션)를 방해하지 않게 한다.
        segment = Segment(id="p1", target_version_id=tv.id, index=0, start=0.0, end=5.0)
        session.add(segment)
        await session.flush()
        claude_f = FindingRow(
            id="f_claude", target_version_id=tv.id, segment_id="p1",
            category="mistranslation", description="클로드 근거",
            original_text="a", suggested_text="texto de claude",
            confidence=0.9, model="claude", status="pending",
        )
        gpt_f = FindingRow(
            id="f_gpt", target_version_id=tv.id, segment_id="p1",
            category="unnatural_style", description="지피티 근거",
            original_text="a", suggested_text="texto de gpt",
            confidence=0.9, model="gpt", status="pending",
        )
        session.add_all([claude_f, gpt_f])
        await session.commit()
    return "f_claude", "f_gpt"


@pytest.mark.asyncio
async def test_pick_approves_chosen_and_rejects_sibling():
    claude_id, gpt_id = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["picked"]["status"] == "approved"
        assert body["picked"]["final_text"] == "texto de claude"
        assert body["rejected"]["status"] == "rejected"
        assert body["rejected"]["final_text"] == ""

    async with async_session() as session:
        claude_f = await session.get(FindingRow, claude_id)
        gpt_f = await session.get(FindingRow, gpt_id)
        assert claude_f.status == "approved"
        assert gpt_f.status == "rejected"
        assert gpt_f.final_text == ""


@pytest.mark.asyncio
async def test_pick_with_custom_final_text_marks_modified():
    """"직접 수정"으로 고른 경우 — 승인이 아니라 수정(modified)으로 남고,
    최종 텍스트는 검수자가 직접 입력한 문구가 된다."""
    claude_id, gpt_id = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id, "final_text": "texto final"},
        )
        assert r.status_code == 200
        assert r.json()["picked"]["status"] == "modified"
        assert r.json()["picked"]["final_text"] == "texto final"

    async with async_session() as session:
        gpt_f = await session.get(FindingRow, gpt_id)
        assert gpt_f.status == "rejected"


@pytest.mark.asyncio
async def test_pick_returns_404_for_missing_other_finding():
    claude_id, _ = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": "does-not-exist"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_reject_pair_rejects_both_and_keeps_original():
    """회귀(사용자 재현): Claude/GPT 둘 다의 제안보다 원본이 낫다고 판단한
    경우 — 개별 카드를 하나씩 거부하지 않아도 한 번에 둘 다 거부하고
    원본을 유지할 수 있어야 한다."""
    claude_id, gpt_id = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/reject-pair",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id},
        )
        assert r.status_code == 200
        body = r.json()
        assert {item["id"]: item["status"] for item in body["rejected"]} == {
            claude_id: "rejected", gpt_id: "rejected",
        }

    async with async_session() as session:
        claude_f = await session.get(FindingRow, claude_id)
        gpt_f = await session.get(FindingRow, gpt_id)
        assert claude_f.status == "rejected"
        assert claude_f.final_text == ""
        assert claude_f.reviewer_name == "김검수"
        assert gpt_f.status == "rejected"
        assert gpt_f.final_text == ""


@pytest.mark.asyncio
async def test_reject_pair_returns_404_for_missing_other_finding():
    claude_id, _ = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/reject-pair",
            json={"reviewer_name": "김검수", "other_finding_id": "does-not-exist"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_approving_finding_auto_shrinks_text_over_line_length(monkeypatch):
    """회귀: Claude/GPT 단독 제안(pending)은 S4 안전망을 거치지 않아 50자를
    넘는 채로 남을 수 있었다 — 승인 시점에 자동으로 줄여야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    await _make_finding_row("f1")
    async with async_session() as session:
        f = await session.get(FindingRow, "f1")
        # 공백 없는 긴 한 단어라 rewrap_line(줄바꿈 재배치)으로는 못 줄이고
        # LLM 폴백(MockProvider.shrink_line)을 강제로 타게 만든다.
        f.suggested_text = "a" * 70
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
    assert r.status_code == 200
    final_text = r.json()["final_text"]
    assert len(final_text) <= 50
    assert final_text != "a" * 70


@pytest.mark.asyncio
async def test_modifying_with_text_over_line_length_is_rejected_and_not_saved():
    """검수자가 직접 입력한 문구는 자동으로 줄이지 않고, 대신 저장 자체를
    막아야 한다 — 임의로 잘라버리면 검수자의 의도와 다른 문장이 나갈 수
    있다."""
    await _make_finding_row("f1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "modified", "reviewer_name": "김검수", "final_text": "a" * 60},
        )
    assert r.status_code == 400

    async with async_session() as session:
        f = await session.get(FindingRow, "f1")
        assert f.status == "pending"
        assert f.final_text == ""


@pytest.mark.asyncio
async def test_pick_auto_shrinks_accepted_suggestion_over_line_length(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    claude_id, gpt_id = await _make_disagreeing_pair()
    async with async_session() as session:
        f = await session.get(FindingRow, claude_id)
        f.suggested_text = "a" * 70
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id},
        )
    assert r.status_code == 200
    assert len(r.json()["picked"]["final_text"]) <= 50


@pytest.mark.asyncio
async def test_pick_with_custom_text_over_line_length_is_rejected_and_not_saved():
    claude_id, gpt_id = await _make_disagreeing_pair()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id, "final_text": "a" * 60},
        )
    assert r.status_code == 400

    async with async_session() as session:
        claude_f = await session.get(FindingRow, claude_id)
        gpt_f = await session.get(FindingRow, gpt_id)
        assert claude_f.status == "pending"
        assert gpt_f.status == "pending"  # 짝도 아직 거부되면 안 됨(저장 자체가 안 됐음)
