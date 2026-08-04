"""전 구간 HTTP E2E: 작품 생성 → 화 생성 → 대상언어 버전 생성 → 분석 실행 →
finding 조회 → 검수 액션 → export 까지를 전부 실제 엔드포인트로 통과시킨다.

기존 API 테스트는 모두 session.add()로 행을 직접 심어 놓고 한 엔드포인트만
호출했고, test_e2e_sample.py는 DB/HTTP 없이 run_pipeline만 인프로세스로
돌렸다. 그래서 "앞 엔드포인트가 만든 것을 뒤 엔드포인트가 실제로 읽을 수
있는가"는 어디에서도 검증되지 않았다. 목킹은 이 코드베이스의 기존 경계인
프로바이더(및 ffmpeg 호출인 extract_audio)까지만 한다.
"""
import asyncio
import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.db import engine, async_session
from app.models import Base, Segment, ExportRow
from app.core.uploads import MEDIA_ROOT

# MockProvider.transcribe는 한국어 세그먼트를 [0.0, 2.0] 하나만 돌려준다.
# 아래 SRT의 대상언어 큐는 어느 것도 그 구간과 겹치지 않으므로:
#   - 한국어 세그먼트는 짝을 못 찾아 target_text가 빈 "고아" 세그먼트가 된다
#     (Fix 3: 이건 export에서 빈 큐로 나가면 안 된다)
#   - 두 대상언어 큐는 짝 없는 target-only 세그먼트로 뒤에 붙는다
# 1번 큐의 온점 4개는 자동보정 대상이라 formatting finding을 만들고,
# 2번 큐는 두 줄짜리라 Fix 2(줄바꿈 보존)를 전 구간에서 검증한다.
TARGET_SRT = """1
00:00:03,000 --> 00:00:05,000
BAD_TRANSLATION aquí....

2
00:00:06,000 --> 00:00:08,000
Primera línea corta
Segunda línea corta
"""


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_full_http_flow_from_title_creation_to_export(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    fake_proxy_path = str(MEDIA_ROOT / "video_proxy" / "fake_proxy.mp4")
    transport = ASGITransport(app=app)
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value=fake_proxy_path), \
         patch("app.background.delete_original_video", return_value=None):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1) 작품
            r = await client.post("/titles", json={"name": "The Peach Tree", "type": "movie"})
            assert r.status_code == 200
            title_id = r.json()["id"]

            # 2) 화
            r = await client.post(f"/titles/{title_id}/episodes",
                                  json={"episode_no": 1, "video_path": "/fake/video.mp4"})
            assert r.status_code == 200
            episode_id = r.json()["id"]

            # 3) 대상언어 버전
            r = await client.post(f"/episodes/{episode_id}/target-versions",
                                  json={"target_language": "es", "variant": "LATAM"})
            assert r.status_code == 200
            tv_id = r.json()["id"]
            assert r.json()["status"] == "analyzing"

            # 4) 분석 실행 (asyncio.create_task로 백그라운드에서 실행되며,
            # 테스트에서는 background_tasks를 대기해 완료를 보장한다)
            r = await client.post(f"/target-versions/{tv_id}/run-analysis",
                                  json={"target_srt_path": str(srt_path)})
            assert r.status_code == 200
            assert r.json()["status"] == "analyzing"

            # background task들이 완료될 때까지 대기한다.
            # 패치가 활성 상태인 동안 waiting하므로, background task가
            # extract_audio를 호출할 때 여전히 mock이 활성이다.
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.json()["status"] == "review"

            # 5) 분석 산출물 조회
            r = await client.get(f"/target-versions/{tv_id}/segments")
            assert r.status_code == 200
            segments = r.json()
            # 한국어 고아 1개 + 대상언어 전용 2개
            assert len(segments) == 3
            assert sum(1 for s in segments if s["target_text"] == "") == 1
            # Fix 2: 두 줄 큐의 줄바꿈이 DB까지 살아 있어야 한다.
            two_line = [s for s in segments if "\n" in s["target_text"]]
            assert len(two_line) == 1
            assert two_line[0]["target_text"] == "Primera línea corta\nSegunda línea corta"

            r = await client.get(f"/target-versions/{tv_id}/findings")
            assert r.status_code == 200
            findings = r.json()
            by_category = {f["category"]: f for f in findings}
            # Fix 4: 포맷 위반이 formatting finding으로 영속화된다.
            assert "translation" in by_category
            assert "formatting" in by_category
            # 자동보정된 온점 위반은 검수자 판단이 필요 없으므로 검수 액션 전에
            # 이미 approved 상태여야 한다. LLM 제안인 translation은 pending.
            assert by_category["formatting"]["status"] == "approved"
            assert by_category["formatting"]["final_text"] == "BAD_TRANSLATION aquí..."
            assert by_category["translation"]["status"] == "pending"

            # Fix 4: 인물이 title 단위로 영속화돼 조회된다.
            r = await client.get(f"/target-versions/{tv_id}/characters")
            assert r.status_code == 200
            characters = r.json()
            assert characters and characters[0]["confirmed_gender"] is None

            # 6) 검수 액션 (승인 → suggested_text가 final_text가 된다)
            translation_finding = by_category["translation"]
            r = await client.post(f"/findings/{translation_finding['id']}/review-action",
                                  json={"action": "approved", "reviewer_name": "검수자A"})
            assert r.status_code == 200
            final_text = r.json()["final_text"]
            assert final_text == translation_finding["suggested_text"] == "texto corregido"

            # 7) export
            r = await client.get(f"/target-versions/{tv_id}/export")
            assert r.status_code == 200
            body = r.json()

    srt = body["srt"]
    # Fix 3: 빈 target_text 세그먼트는 큐로 나가지 않는다 (3개 중 2개만).
    assert srt.count("-->") == 2
    assert "00:00:00,000 --> 00:00:02,000" not in srt
    # 승인된 수정본이 반영된다.
    assert "texto corregido" in srt
    assert "BAD_TRANSLATION" not in srt
    # Fix 2: 두 줄 큐가 두 줄로 나간다.
    assert "Primera línea corta\nSegunda línea corta" in srt
    # Fix 3: 타임코드 순.
    assert srt.index("texto corregido") < srt.index("Primera línea corta")

    # 통계는 실제 저장된 finding 기준이어야 한다. 2건 모두 반영됐다 —
    # translation은 검수자가 승인했고, 자동보정된 formatting은 저장 시점에
    # 이미 approved였다.
    assert body["stats"]["finding_count"] == len(findings) == 2
    assert body["stats"]["reflection_rate"] == 1.0

    # Fix 6: export 이력이 남는다.
    async with async_session() as session:
        exports = list((await session.execute(
            select(ExportRow).where(ExportRow.target_version_id == tv_id)
        )).scalars().all())
        seg_rows = list((await session.execute(
            select(Segment).where(Segment.target_version_id == tv_id)
        )).scalars().all())
    assert len(exports) == 1
    assert exports[0].finding_count == body["stats"]["finding_count"]
    # Fix 1: 저장된 segment id는 target_version_id로 네임스페이싱된다.
    assert all(s.id.startswith(f"{tv_id}:") for s in seg_rows)
