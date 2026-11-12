import pytest
from sqlalchemy import select
from app.db import async_session, engine
from app.models import Base, Title, Episode, TargetVersion, Segment, FindingRow, GlossaryFix
from app.repositories import add_cross_episode_findings

KO = "아빠는 팔 안 아파"


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _tv(session, title_id, episode_no, segs, lang="fr", variant="FR"):
    """segs: [(segment_id, korean, target)] → target_version id."""
    ep = Episode(title_id=title_id, episode_no=episode_no, video_path="/x.mp4")
    session.add(ep); await session.flush()
    tv = TargetVersion(episode_id=ep.id, target_language=lang, variant=variant)
    session.add(tv); await session.flush()
    for i, (sid, ko, tgt) in enumerate(segs):
        session.add(Segment(id=sid, target_version_id=tv.id, index=i, start=125.0 + i, end=127.0 + i,
                            korean_text=ko, target_text=tgt))
    await session.flush()
    return tv.id


async def _findings(session, tv_id):
    return (await session.execute(select(FindingRow).where(FindingRow.target_version_id == tv_id))).scalars().all()


@pytest.mark.asyncio
async def test_differing_translation_of_earlier_episode_line_becomes_finding():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        await _tv(session, title.id, 1, [("e1s1", KO, "Papa n'a pas mal au bras")])
        cur = await _tv(session, title.id, 3, [("e3s1", KO + "!", "Papa ne ressent rien"), ("e3s2", "전혀 다른 대사 입니다 정말", "Autre")])
        assert await add_cross_episode_findings(session, cur) == 1
        (f,) = await _findings(session, cur)
        assert (f.segment_id, f.category, f.source, f.status) == ("e3s1", "consistency", "rule", "pending")
        assert (f.original_text, f.suggested_text) == ("Papa ne ressent rien", "Papa n'a pas mal au bras")
        assert "1화" in f.description and "02:05" in f.description


@pytest.mark.asyncio
async def test_uses_earlier_episode_final_text_after_review_and_fixes():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        prev = await _tv(session, title.id, 1, [("e1s1", KO, "Papa a mal"), ("e1s2", KO + " 정말", "Papa a mal 2")])
        session.add(FindingRow(id="f1", target_version_id=prev, segment_id="e1s1", category="mistranslation",
                               description="d", original_text="Papa a mal", suggested_text="Papa n'a pas mal",
                               confidence=0.9, status="approved", final_text="Papa n'a pas mal"))
        session.add(GlossaryFix(target_version_id=prev, segment_id="e1s2", kind="repeated",
                                before_text="Papa a mal 2", after_text="Papa n'a pas mal"))
        cur = await _tv(session, title.id, 2, [("e2s1", KO, "Papa n'a pas mal")])
        assert await add_cross_episode_findings(session, cur) == 0  # 검수 결과·적용한 수정이 반영된 최종 번역과 같다


@pytest.mark.asyncio
async def test_ignores_other_language_later_episodes_and_missing_episode_no():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        await _tv(session, title.id, 1, [("a", KO, "Papa a mal")], lang="es", variant="LATAM")
        await _tv(session, title.id, 5, [("b", KO, "Papa a mal")])
        await _tv(session, title.id, None, [("c", KO, "Papa a mal")])
        cur = await _tv(session, title.id, 3, [("d", KO, "Papa ne ressent rien")])
        assert await add_cross_episode_findings(session, cur) == 0


@pytest.mark.asyncio
async def test_most_recent_earlier_episode_is_the_reference():
    async with async_session() as session:
        title = Title(name="T", type="series"); session.add(title); await session.flush()
        await _tv(session, title.id, 1, [("a", KO, "Version un")])
        await _tv(session, title.id, 2, [("b", KO, "Version deux")])
        cur = await _tv(session, title.id, 3, [("c", KO, "Version trois")])
        await add_cross_episode_findings(session, cur)
        (f,) = await _findings(session, cur)
        assert f.suggested_text == "Version deux" and "2화" in f.description
