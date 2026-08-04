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
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
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
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
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
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
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
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
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
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
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


@pytest.mark.asyncio
async def test_pipeline_continues_when_build_registry_raises(tmp_path, monkeypatch):
    """C2 회귀: build_registry(analyze_characters, 실제 LLM 네트워크 호출)가
    실패해도 전체 분석이 실패 처리되면 안 된다 — Claude/GPT 패스와 동일한 부분
    실패 허용 원칙이 인물/관계 식별에도 적용돼야 한다. 실패 시 빈 레지스트리로
    진행하고, 이미 든 STT 비용을 낭비하지 않도록 나머지 파이프라인(Claude/GPT/
    안전망)은 계속 실행돼야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _analyze_characters_raises(*args, **kwargs):
        raise RuntimeError("인물 식별 API 오류")

    monkeypatch.setattr(provider, "analyze_characters", _analyze_characters_raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    assert result["characters"] == []
    assert result["relationships"] == []
    # GPT 2차는 계속 실행되어 BAD_TRANSLATION 마커를 정상 탐지한다 — 인물 식별
    # 실패가 나머지 파이프라인을 막지 않았다는 증거.
    assert any(f.model == "gpt" for f in result["findings"])


@pytest.mark.asyncio
async def test_pipeline_does_not_delete_original_video(tmp_path, monkeypatch):
    """C2 회귀: run_pipeline 자신은 더 이상 원본 영상을 지우지 않는다 — 결과가
    호출자(background.py)에 의해 실제로 커밋된 뒤에만 지워야 하기 때문이다.
    대신 video_path를 결과에 그대로 담아 반환해, 호출자가 커밋 이후 언제
    지울지 스스로 결정할 수 있게 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path=str(video),
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
        )

    assert video.exists()
    assert result["video_path"] == str(video)


@pytest.mark.asyncio
async def test_pipeline_cleans_up_orphaned_proxy_when_transcribe_fails(tmp_path, monkeypatch):
    """I6 회귀: asyncio.gather로 STT와 영상 프록시 생성을 동시에 돌릴 때,
    to_thread로 감싼 generate_video_proxy는 취소할 수 없다 — transcribe가
    먼저 실패해도 프록시 생성 스레드는 계속 돌아 파일을 만들어낼 수 있다.
    run_pipeline이 실패로 끝나면 아무도 참조하지 않을 그 프록시 파일이
    고아로 남으면 안 된다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    proxy_path = tmp_path / "orphan_proxy.mp4"

    def _fake_generate_proxy(video_path, out_dir=None):
        proxy_path.write_bytes(b"fake proxy")
        return str(proxy_path)

    async def _transcribe_raises(*args, **kwargs):
        raise RuntimeError("STT API 오류")

    provider = MockProvider()
    monkeypatch.setattr(provider, "transcribe", _transcribe_raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", side_effect=_fake_generate_proxy):
        with pytest.raises(RuntimeError, match="STT API 오류"):
            await run_pipeline(
                video_path="/fake/video.mp4",
                target_srt_path=str(srt_path),
                language="es", variant="LATAM",
                target_version_id="tv1", provider=provider,
            )

    assert not proxy_path.exists()


@pytest.mark.asyncio
async def test_pipeline_gpt_original_reference_reflects_post_pretreatment_text(
        tmp_path, monkeypatch):
    """I7 회귀: GPT 2차가 "1차 교정자가 뭔가 잘못 고쳤는지" 대조하는 데 쓰는
    original_target_by_id는 사전필터(#3/#4/#6, 정책적 편집)까지 적용된 뒤의
    텍스트여야 한다. 사전필터 이전의 진짜 원본을 기준으로 삼으면, GPT가
    정책적으로 이미 치환/삭제된 내용을 "복원"하도록 유도될 수 있다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBAD_TRANSLATION mierda....\n", encoding="utf-8")
    provider = MockProvider()

    captured = {}

    async def _capture_verify_and_refine(pairs, original_target_by_id, *args, **kwargs):
        captured["original_target_by_id"] = dict(original_target_by_id)
        return []

    monkeypatch.setattr(provider, "verify_and_refine", _capture_verify_and_refine)
    monkeypatch.setattr(
        "app.core.pipeline.load_profanity_dictionary",
        lambda: [{"term": "mierda", "replacement": "[삐-]"}],
    )

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    original_text = next(iter(captured["original_target_by_id"].values()))
    assert "mierda" not in original_text
    assert "[삐-]" in original_text


@pytest.mark.asyncio
async def test_pipeline_returns_raw_korean_segments_for_caching(tmp_path):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
        )

    assert result["korean_segments_raw"] == [{"start": 0.0, "end": 2.0, "text": "안녕하세요"}]


@pytest.mark.asyncio
async def test_pipeline_uses_cached_stt_and_skips_transcribe_and_proxy_generation(tmp_path):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")

    with patch("app.core.pipeline.extract_audio") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy") as mock_proxy:
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
            cached_korean_segments=[{"start": 0.0, "end": 2.0, "text": "캐시된 대사"}],
            cached_video_proxy_path="/fake/cached_proxy.mp4",
        )

    mock_extract.assert_not_called()
    mock_proxy.assert_not_called()
    assert result["video_proxy_path"] == "/fake/cached_proxy.mp4"
    korean_pair = next(p for p in result["pairs"] if p.korean is not None)
    assert korean_pair.korean.text == "캐시된 대사"
