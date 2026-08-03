import pytest
from unittest.mock import patch
from app.core.pipeline import run_pipeline
from app.providers.mock import MockProvider

TARGET_SRT = """1
00:00:00,000 --> 00:00:02,000
BAD_TRANSLATION aquí....
"""


@pytest.mark.asyncio
async def test_pipeline_produces_findings_and_format_violations(tmp_path):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1",
            provider=MockProvider(),
        )

    assert any(f.category == "translation" and f.model == "gpt" for f in result["findings"])
    assert any(v.rule == "ellipsis" for v in result["format_violations"])
    assert result["video_proxy_path"] == "/fake/proxy.mp4"
    assert "pairs" in result


@pytest.mark.asyncio
async def test_pipeline_applies_claude_correction_before_gpt_sees_it(tmp_path, monkeypatch):
    """GPT 2차는 Claude 1차가 이미 고친 텍스트를 이어받아야 한다 — 순차 구조의
    핵심 계약. MockProvider.verify_and_refine은 current_text에 BAD_TRANSLATION이
    남아있을 때만 찾아내므로, Claude가 먼저 그 마커를 지우면 GPT 쪽 finding이
    생기지 않아야 순차 전달이 실제로 일어났음을 확인할 수 있다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_removes_marker(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "sensitivity",
                  "corrected_text": pairs[0]["target_text"].replace("BAD_TRANSLATION", "texto"),
                  "description": "마커 제거"}]

    monkeypatch.setattr(provider, "correct_primary", _claude_removes_marker)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    assert not any(f.model == "gpt" for f in result["findings"])
    assert any(f.model == "claude" for f in result["findings"])


@pytest.mark.asyncio
async def test_pipeline_continues_when_claude_pass_raises(tmp_path, monkeypatch):
    """design §에러 처리: Claude/GPT 패스 중 하나가 실패해도 전체 분석이
    실패 처리되면 안 되고, 해당 패스만 스킵한 채 나머지 파이프라인(GPT 2차,
    안전망)은 계속 진행돼야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_raises(*args, **kwargs):
        raise RuntimeError("Claude API 오류")

    monkeypatch.setattr(provider, "correct_primary", _claude_raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    assert not any(f.model == "claude" for f in result["findings"])
    # GPT 2차는 Claude가 건너뛰었으므로 사전필터 이후 원본 텍스트를 그대로
    # 이어받아 실행되고, BAD_TRANSLATION 마커가 여전히 남아 있으므로 정상 탐지된다.
    assert any(f.model == "gpt" for f in result["findings"])


@pytest.mark.asyncio
async def test_pipeline_continues_when_gpt_pass_raises(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _gpt_raises(*args, **kwargs):
        raise RuntimeError("GPT API 오류")

    monkeypatch.setattr(provider, "verify_and_refine", _gpt_raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    assert not any(f.model == "gpt" for f in result["findings"])
    assert "pairs" in result


@pytest.mark.asyncio
async def test_pipeline_rechecks_ellipsis_after_gpt_pass(tmp_path, monkeypatch):
    """design §핵심 설계 포인트: GPT 패스가 문장을 늘리며 온점 4개 이상을 새로
    만들 수 있으므로, 온점은 맨 처음뿐 아니라 모든 교정이 끝난 뒤 한 번 더
    검사·자동보정해야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _gpt_introduces_ellipsis(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "translation",
                  "corrected_text": "espera......", "description": "GPT가 늘어뜨림"}]

    monkeypatch.setattr(provider, "verify_and_refine", _gpt_introduces_ellipsis)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    final_pair = next(p for p in result["pairs"] if p.target is not None)
    assert final_pair.target.text == "espera..."
    ellipsis_violations = [v for v in result["format_violations"] if v.rule == "ellipsis"]
    assert len(ellipsis_violations) == 2
    # 회귀(Important): 두 체크포인트의 original_text는 각자 그 시점의 텍스트를
    # 반영해야 한다 — 파이프라인 최종 상태 하나로 되짚어 재구성하면 둘 다 같은
    # (그리고 대부분 틀린) 값이 된다. 첫 체크포인트는 Claude/GPT 이전의 원문,
    # 두 번째(S4 최종 재체크)는 GPT가 새로 늘어뜨린 뒤 최종 온점 자동보정 직전의
    # 텍스트여야 하며, 서로 달라야 한다.
    first_checkpoint = next(v for v in ellipsis_violations if v.original_text.startswith("BAD_TRANSLATION"))
    second_checkpoint = next(v for v in ellipsis_violations if v.original_text.startswith("espera"))
    assert first_checkpoint.original_text == "BAD_TRANSLATION aquí...."
    assert second_checkpoint.original_text == "espera......"
    assert first_checkpoint.original_text != second_checkpoint.original_text
