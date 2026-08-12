import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import Base, TargetVersion, Episode, Title, Segment, SttCorrection, FindingRow
import app.routers.findings as findings_router


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_correct_stt_updates_segment_and_records_correction(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안뇽하세요", target_text="Hola")
        session.add(seg); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "안녕하세요", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        assert r.json()["korean_text"] == "안녕하세요"

    async with async_session() as session:
        rows = await session.execute(
            select(SttCorrection).where(SttCorrection.segment_id == seg_id)
        )
        corrections = list(rows.scalars().all())
        assert len(corrections) == 1
        assert corrections[0].original_text == "안뇽하세요"
        assert corrections[0].corrected_text == "안녕하세요"
        assert corrections[0].reviewer_name == "김검수"


@pytest.mark.asyncio
async def test_correct_stt_creates_pending_finding_when_reverify_flags_problem(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        # MockProvider는 target_text에 "BAD_TRANSLATION" 마커가 있으면 항상
        # 교정을 돌려준다 — 재검증이 문제를 찾은 경우를 결정론적으로 재현.
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="안뇽하세요", target_text="BAD_TRANSLATION")
        session.add(seg); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "안녕하세요", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["new_finding"] is not None
        assert body["new_finding"]["suggested_text"] == "texto corregido"

    async with async_session() as session:
        rows = await session.execute(
            select(FindingRow).where(FindingRow.segment_id == seg_id)
        )
        findings = list(rows.scalars().all())
        assert len(findings) == 1
        assert findings[0].status == "pending"
        assert findings[0].model == "gpt"
        # 검수자가 스페인어를 몰라도 "원본"이 무슨 뜻인지 알 수 있어야 하니,
        # 원본(BAD_TRANSLATION)의 한국어 역번역이 description에 붙어야 한다.
        assert "원본 한국어 역번역 참고: [역번역:BAD_TRANSLATION]" in findings[0].description


@pytest.mark.asyncio
async def test_correct_stt_updates_existing_finding_in_place_instead_of_duplicating(monkeypatch):
    """회귀(사용자 재현): 세그먼트에 이미 승인된 finding이 하나 있는 상태에서
    STT를 수정해 재검증이 새 문제를 찾으면, 별도의 새 카드를 또 만들지 않고
    기존 카드를 그 자리에서 pending으로 되돌려 갱신해야 한다. 새로 만들면
    같은 세그먼트에 "승인됨" finding이 두 개 남는데, export의
    _final_text_by_segment는 reviewed_at 유무만 boolean 비교라(실제 시각
    순서 비교 아님) 어느 쪽이 최종 텍스트로 뽑힐지 보장이 안 된다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async def fake_reverify(segment, provider, knowledge, profile):
        return {"category": "mistranslation", "description": "재검증 설명",
                "corrected_text": "Ya revisé todo."}

    monkeypatch.setattr(findings_router, "reverify_segment_after_stt_correction", fake_reverify)

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="확인했냐고 물어봄", target_text="¿Revisaste de nuevo?")
        session.add(seg); await session.flush()
        existing_finding = FindingRow(
            id="existing_finding_1", target_version_id=tv.id, segment_id=seg.id,
            category="mistranslation", description="기존 오역 지적",
            original_text="원본", suggested_text="¿Revisaste de nuevo?",
            confidence=1.0, source="llm", model="claude+gpt",
            status="approved", final_text="¿Revisaste de nuevo?",
            reviewer_name="김검수", reviewed_at=None,
        )
        session.add(existing_finding); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "확인 다 해봤어?", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["new_finding"]["id"] == "existing_finding_1"

    async with async_session() as session:
        rows = await session.execute(
            select(FindingRow).where(FindingRow.segment_id == seg_id)
        )
        findings = list(rows.scalars().all())
        assert len(findings) == 1  # 새로 만들어지지 않고 기존 것 하나만 남음
        assert findings[0].id == "existing_finding_1"
        assert findings[0].status == "pending"
        assert findings[0].suggested_text == "Ya revisé todo."
        assert findings[0].final_text == ""
        assert findings[0].reviewer_name == ""


@pytest.mark.asyncio
async def test_correct_stt_reapplies_already_confirmed_gender_to_new_suggestion(monkeypatch):
    """회귀: 1차 검수 때 이미 확정된 성별(resolved_gender_raw)이 STT
    재검증이 새로 만든 제안문구에도 반영돼야 한다 — 안 그러면 "cansado/a"
    같은 미확정 표기가 그대로 새어나간다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async def fake_reverify(segment, provider, knowledge, profile):
        return {"category": "mistranslation", "description": "테스트",
                "corrected_text": "Te ves cansado/a."}

    monkeypatch.setattr(findings_router, "reverify_segment_after_stt_correction", fake_reverify)

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="피곤해 보이네", target_text="Hola.",
                      gender_check_needed=True, resolved_gender_raw="male")
        session.add(seg); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "너 피곤해 보이네", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["new_finding"] is not None
        assert body["new_finding"]["suggested_text"] == "Te ves cansado."

    async with async_session() as session:
        rows = await session.execute(
            select(FindingRow).where(FindingRow.segment_id == seg_id)
        )
        findings = list(rows.scalars().all())
        assert len(findings) == 1
        assert findings[0].suggested_text == "Te ves cansado."

        seg = await session.get(Segment, seg_id)
        # 원래 확정돼 있던 성별로 커버되는 경우라 새로 확인이 필요해지면 안 된다.
        assert not seg.resolved_gender_groups_raw


@pytest.mark.asyncio
async def test_correct_stt_flags_new_gender_ambiguity_when_not_covered_by_prior_resolution(monkeypatch):
    """1차 검수 때는 원문에 성별 표시 단어가 없어 gender_check_needed가
    False였는데, STT 재검증이 새로 만든 문장에 성별 표시 단어가 처음
    등장하는 경우 — 재적용할 기존 값이 없으므로 사람에게 새로 확인받아야
    한다."""
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")

    async def fake_reverify(segment, provider, knowledge, profile):
        return {"category": "mistranslation", "description": "테스트",
                "corrected_text": "Estoy cansado."}

    monkeypatch.setattr(findings_router, "reverify_segment_after_stt_correction", fake_reverify)

    async with async_session() as session:
        title = Title(name="T", type="movie"); session.add(title); await session.flush()
        episode = Episode(title_id=title.id, video_path="/x.mp4"); session.add(episode); await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM")
        session.add(tv); await session.flush()
        seg = Segment(target_version_id=tv.id, index=0, start=0.0, end=2.0,
                      korean_text="여기 있어요", target_text="Aquí está.",
                      gender_check_needed=False, resolved_gender_raw=None)
        session.add(seg); await session.commit()
        seg_id = seg.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            f"/segments/{seg_id}/correct-stt",
            json={"corrected_text": "저 피곤해요", "reviewer_name": "김검수"},
        )
        assert r.status_code == 200

    async with async_session() as session:
        seg = await session.get(Segment, seg_id)
        assert seg.gender_check_needed is True
        assert seg.resolved_gender_groups_raw
        # 회귀(사용자 재현): 새로 생긴 그룹도 메인 파이프라인의 성별 확인과
        # 똑같이 단어 뜻풀이가 채워져야 한다 — 스페인어를 모르는 검수자가
        # "cansado"만 보고는 사람 얘기인지조차 판단 못 한다.
        group = seg.resolved_gender_groups_raw[0]
        assert group["word_meanings"] == {"cansado": "[뜻:cansado]"}
        # 회귀(사용자 재현): "이게 누구 얘기인지"도 메인 파이프라인처럼 같이
        # 나와야 한다 — 새 설계에서는 숫자 인칭 대신 LLM이 준 referent
        # 설명 문자열이다.
        assert group["referent"] == "인물1"
        assert seg.resolved_gender_groups_raw[0]["gender"] is None
