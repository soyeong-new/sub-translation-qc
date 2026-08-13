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
from app.providers.mock import MockProvider

# MockProvider.transcribe는 기본적으로 한국어 세그먼트를 [0.0, 2.0] 하나만
# 돌려주는데, 이 테스트는 patch(_transcribe_with_extra_korean_word)로 BAD_
# TRANSLATION 큐([3,5])와 겹치는 단어를 하나 더 추가한다 — 한국어 원문이
# 없는 pair는 S2(AI 이중검증)를 건너뛰므로, 오역 finding 흐름을 검증하려면
# 그 pair에 한국어가 있어야 한다.
#   - [0.0, 2.0] 단어는 그대로 짝을 못 찾아 target_text가 빈 "고아"
#     세그먼트가 된다(Fix 3: 이건 export에서 빈 큐로 나가면 안 된다)
#   - [3.0, 4.0] 단어는 1번 큐(BAD_TRANSLATION)와 짝지어져 그 pair가
#     한국어 원문을 갖게 되고, S2가 정상적으로 오역을 지적한다
#   - 2번 큐(두 줄짜리)는 짝 없는 target-only 세그먼트로 남아 Fix 2
#     (줄바꿈 보존)와 반쪽짜리 처리를 전 구간에서 검증한다
TARGET_SRT = """1
00:00:03,000 --> 00:00:05,000
BAD_TRANSLATION aquí....

2
00:00:06,000 --> 00:00:08,000
Primera línea corta
Segunda línea corta
"""


async def _transcribe_with_extra_korean_word(self, audio_path):
    """기본 MockProvider.transcribe([0.0,2.0] 한 단어)에 하나를 더해 —
    BAD_TRANSLATION 큐([3,5])와 겹치는 한국어 단어를 만든다. 한국어 원문이
    없는 pair는 S2(AI 이중검증)에서 건너뛰므로(design 2026-08-13-korean-
    srt-cue-based-segmentation-design.md §스페인어만 있는 경우), 이 테스트가
    검증하려는 "오역 finding 흐름"이 실제로 발생하려면 그 pair에 한국어
    텍스트가 있어야 한다. [0.0,2.0] 단어는 그대로 둬 한국어 전용 고아
    세그먼트 커버리지를 유지한다."""
    return [
        {"start": 0.0, "end": 2.0, "text": "안녕하세요"},
        {"start": 3.0, "end": 4.0, "text": "hola"},
    ]


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
         patch("app.background.delete_original_video", return_value=None), \
         patch.object(MockProvider, "transcribe", _transcribe_with_extra_korean_word):
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

            # "Segunda línea corta"의 "corta"가 성별 표시 형용사라 성별 확인이
            # 걸린다 — 한국어 원문이 없는 대상언어 전용 세그먼트라 자동 판정도
            # 안 되므로, 사람이 답할 때까지 S2(AI 검증)는 시작되지 않는다.
            r = await client.get(f"/target-versions/{tv_id}")
            assert r.json()["status"] == "awaiting_confirmation"

            r = await client.get(f"/target-versions/{tv_id}/flagged-segments")
            for seg in r.json():
                if seg["gender_check_needed"] and not seg["resolved_gender_raw"]:
                    r2 = await client.post(f"/segments/{seg['id']}/resolve-gender",
                                           json={"gender": "female"})
                    assert r2.status_code == 200
                if seg["formality_check_needed"] and not seg["resolved_formality_raw"]:
                    r2 = await client.post(f"/segments/{seg['id']}/resolve-formality",
                                           json={"formality_level": "informal"})
                    assert r2.status_code == 200

            # 확인이 끝난 뒤에야 confirm-registers로 S2를 시작할 수 있다.
            r = await client.post(f"/target-versions/{tv_id}/confirm-registers")
            assert r.status_code == 200
            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

            r = await client.get(f"/target-versions/{tv_id}")
            assert r.json()["status"] == "review"

            # 5) 분석 산출물 조회
            r = await client.get(f"/target-versions/{tv_id}/segments")
            assert r.status_code == 200
            segments = r.json()
            # 한국어 고아 1개 + 한국어와 짝지어진 세그먼트 1개 + 대상언어 전용 1개
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
            assert "mistranslation" in by_category
            assert "formatting" in by_category
            # 자동보정된 온점 위반은 검수자 판단이 필요 없으므로 검수 액션 전에
            # 이미 approved 상태여야 한다. mistranslation도 Claude/GPT 둘 다
            # BAD_TRANSLATION 마커를 지적해 합의됐으므로 이미 approved다 —
            # 스페인어를 모르는 검수자는 텍스트 품질을 판단할 수 없으므로,
            # 합의된 교정은 사람 승인 없이 자동 적용된다(design §어떻게 사용).
            assert by_category["formatting"]["status"] == "approved"
            assert by_category["formatting"]["final_text"] == "BAD_TRANSLATION aquí..."
            assert by_category["mistranslation"]["status"] == "approved"

            # 6) 검수 액션 (재승인해도 idempotent하게 동작해야 한다)
            translation_finding = by_category["mistranslation"]
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
