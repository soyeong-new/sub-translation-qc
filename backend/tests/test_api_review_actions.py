import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import (
    Base, FindingRow, Title, Episode, TargetVersion, Segment,
    GlossaryEntry, GlossarySpelling,
)


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


_LONG_TWO_SENTENCES = "Esto es una frase bastante larga que continúa. Esta es otra frase igualmente larga que sigue aquí."


@pytest.mark.asyncio
async def test_approving_finding_over_line_length_splits_the_cue_instead_of_shrinking():
    """Claude/GPT 단독 제안(pending)은 S4 안전망을 거치지 않아 2줄×42자를 넘을 수 있다 —
    승인 시점에 LLM으로 줄이지 않고 큐를 나눈다(내용 보존)."""
    await _make_finding_row("f1")
    async with async_session() as session:
        f = await session.get(FindingRow, "f1")
        f.suggested_text = _LONG_TWO_SENTENCES
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
    body = r.json()
    assert r.status_code == 200 and body["status"] == "approved"
    assert body["final_text"].startswith("Esto es una frase") and body["split"]["new"]["target_text"].startswith("Esta es otra")
    async with async_session() as session:
        assert len((await session.execute(select(Segment))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_approving_unsplittable_over_length_text_is_kept_without_llm():
    await _make_finding_row("f1")
    async with async_session() as session:
        f = await session.get(FindingRow, "f1")
        f.suggested_text = "a" * 70  # 공백 없는 한 단어 — 재배치도 분할도 못 한다
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
    assert r.status_code == 200
    assert r.json()["final_text"] == "a" * 70 and r.json()["split"] is None


@pytest.mark.asyncio
async def test_approving_does_not_split_when_another_finding_on_the_cue_is_pending():
    await _make_finding_row("f1")
    async with async_session() as session:
        f = await session.get(FindingRow, "f1")
        f.suggested_text = _LONG_TWO_SENTENCES
        session.add(FindingRow(id="f2", target_version_id=f.target_version_id, segment_id="p1",
                               category="mistranslation", description="근거", original_text="a",
                               suggested_text="b", confidence=0.9))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
    assert r.status_code == 200 and r.json()["split"] is None
    async with async_session() as session:
        assert len((await session.execute(select(Segment))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_modifying_saves_typed_text_exactly_even_when_over_line_length():
    """검수자가 직접 쓴 문구는 줄바꿈도 길이도 건드리지 않고 쓴 그대로 저장한다."""
    await _make_finding_row("f1")
    typed = "Ellos dijeron que nunca me salvaran la vida jamás"   # 49자, 한 줄

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/findings/f1/review-action",
                              json={"action": "modified", "reviewer_name": "김검수", "final_text": typed})
    assert r.status_code == 200
    assert r.json()["final_text"] == typed


@pytest.mark.asyncio
async def test_pick_splits_accepted_suggestion_over_line_length():
    claude_id, gpt_id = await _make_disagreeing_pair()
    async with async_session() as session:
        f = await session.get(FindingRow, claude_id)
        f.suggested_text = _LONG_TWO_SENTENCES
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/findings/{claude_id}/pick",
            json={"reviewer_name": "김검수", "other_finding_id": gpt_id},
        )
    body = r.json()
    assert r.status_code == 200 and body["picked"]["status"] == "approved"
    assert body["picked"]["final_text"].startswith("Esto es una frase")
    assert body["split"]["new"]["target_text"].startswith("Esta es otra")
    assert body["rejected"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_pick_with_custom_text_saves_it_exactly():
    claude_id, gpt_id = await _make_disagreeing_pair()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/findings/{claude_id}/pick",
                              json={"reviewer_name": "김검수", "other_finding_id": gpt_id, "final_text": "a" * 60})
    assert r.status_code == 200
    assert r.json()["picked"]["final_text"] == "a" * 60


async def _make_finding_row_with_glossary(finding_id: str, *, korean_text: str,
                                           original_text: str, suggested_text: str,
                                           korean_term: str, canonical: str) -> None:
    """review-action/pick의 용어집 강제 훅을 테스트하기 위한 세팅 —
    korean_text에 등록 용어가 있고, 이미 확정된 canonical이 있는 상태를
    만든다."""
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
        segment = Segment(id="p1", target_version_id=tv.id, index=0, start=0.0, end=5.0,
                           korean_text=korean_text)
        session.add(segment)
        entry = GlossaryEntry(title_id=title.id, korean_term=korean_term,
                               category="person", aliases=[])
        session.add(entry)
        await session.flush()
        session.add(GlossarySpelling(entry_id=entry.id, language="es", variant="LATAM",
                                      canonical=canonical))
        f = FindingRow(id=finding_id, target_version_id=tv.id, segment_id="p1",
                       category="mistranslation", description="근거",
                       original_text=original_text, suggested_text=suggested_text,
                       confidence=0.9)
        session.add(f)
        await session.commit()


@pytest.mark.asyncio
async def test_approving_finding_enforces_registered_glossary_canonical(monkeypatch):
    """회귀(사용자 보고): "강오크"가 용어집에 등록돼 있어도 AI가 다시
    번역하면서 다른 철자("Gang-ok")를 낼 수 있다 — 승인 시점에 표준 표기로
    강제해야 한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    await _make_finding_row_with_glossary(
        "f1", korean_text="야, 강오크.", original_text="Oye, Kang Hulk.",
        suggested_text="Oye, Gang-ok.", korean_term="강오크", canonical="Kang-ok")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "approved", "reviewer_name": "김검수"},
        )
    assert r.status_code == 200
    assert r.json()["final_text"] == "Oye, Kang-ok."


@pytest.mark.asyncio
async def test_modifying_finding_self_heals_glossary_canonical():
    """검수자가 직접 다른 표기로 고치면, 강제로 되돌리지 않고 그 표기를 새
    표준 표기로 승격한다 — 최초 등록이 틀렸어도 검수 한 번으로 고쳐진다."""
    await _make_finding_row_with_glossary(
        "f1", korean_text="야, 강오크.", original_text="Oye, Kang Hulk.",
        suggested_text="Oye, Gang-ok.", korean_term="강오크", canonical="Kang-ok")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/findings/f1/review-action",
            json={"action": "modified", "reviewer_name": "김검수",
                  "final_text": "Oye, Kang Orc!"},
        )
    assert r.status_code == 200

    async with async_session() as session:
        spelling = (await session.execute(
            select(GlossarySpelling).where(GlossarySpelling.language == "es")
        )).scalar_one()
        assert spelling.canonical == "Orc"


@pytest.mark.asyncio
async def test_rejecting_auto_rewrap_restores_original_text():
    await _make_finding_row("f1")
    async with async_session() as session:
        seg = await session.get(Segment, "p1")
        seg.target_text = "uno dos\ntres"
        f = await session.get(FindingRow, "f1")
        f.model, f.source, f.category = "자동재배치", "rule", "formatting"
        f.original_text, f.suggested_text, f.status = "uno dos tres", "uno dos\ntres", "approved"
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/findings/f1/review-action",
                              json={"action": "rejected", "reviewer_name": "김검수"})
        assert r.status_code == 200

    async with async_session() as session:
        assert (await session.get(Segment, "p1")).target_text == "uno dos tres"
