from pathlib import Path

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


@pytest.mark.asyncio
async def test_pipeline_returns_empty_warnings_when_all_stages_succeed(tmp_path):
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

    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_pipeline_flags_lines_needing_gender_or_formality_check(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nEsta cansada.\n\n"
        "2\n00:00:20,000 --> 00:00:22,000\nHola.\n",
        encoding="utf-8",
    )
    provider = MockProvider()

    async def _flag_first_only(pairs, profile):
        return [
            {"id": p["id"], "gender_check_needed": p["id"] == "pair_1",
             "formality_check_needed": False}
            for p in pairs
        ]

    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_first_only)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    resolutions = result["segment_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["gender_check_needed"] is True
    assert resolutions[0]["formality_check_needed"] is False


@pytest.mark.asyncio
async def test_pipeline_finds_anchor_candidates_for_flagged_scene(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nEsta cansada.\n", encoding="utf-8")
    provider = MockProvider()

    async def _transcribe_with_name(audio_path):
        return [{"start": 0.0, "end": 2.0, "text": "민지야 피곤해 보인다"}]

    async def _flag_all(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False}
                for p in pairs]

    monkeypatch.setattr(provider, "transcribe", _transcribe_with_name)
    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_all)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            prior_characters=[{"id": "c1", "label": "민지"}],
        )

    resolutions = result["segment_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["gender_anchor_candidates"] == [{"id": "c1", "label": "민지"}]


@pytest.mark.asyncio
async def test_pipeline_finds_relationship_anchor_candidates_for_flagged_scene(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n¿Tú o usted?\n", encoding="utf-8")
    provider = MockProvider()

    async def _transcribe_with_names(audio_path):
        return [{"start": 0.0, "end": 2.0, "text": "민지야 서준이한테 존댓말 써야 돼?"}]

    async def _flag_all(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": False, "formality_check_needed": True}
                for p in pairs]

    monkeypatch.setattr(provider, "transcribe", _transcribe_with_names)
    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_all)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            prior_relationships=[
                {"id": "r1", "speaker_label": "민지", "addressee_label": "서준"},
            ],
        )

    resolutions = result["segment_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["formality_anchor_candidates"] == [{"id": "r1", "label": "민지 → 서준"}]
    # gender_anchor_candidates는 formality용과 다른 계산이어야 한다 — 이 세그먼트는
    # gender_check_needed가 False이므로 빈 리스트여야 한다.
    assert resolutions[0]["gender_anchor_candidates"] == []


@pytest.mark.asyncio
async def test_pipeline_batches_check_grammar_necessity_for_long_episodes(tmp_path, monkeypatch):
    """회귀(Finding 2): check_grammar_necessity는 줄 하나당 결과 객체 하나를
    빠짐없이 반환해야 하는 스키마라, 실제 에피소드 분량(수백 줄)을 한 번의
    호출로 보내면 max_tokens을 넘겨 응답이 잘리고 파싱이 실패해 성별/격식
    체크가 통째로 사라진다. 150줄(배치 크기 100의 2배 이상)을 넣어 provider가
    두 번 이상 호출되는지, 그리고 모든 줄이 어느 한 배치에는 빠짐없이
    포함되는지 확인한다."""
    def _timestamp(total_seconds: int) -> str:
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},000"

    LINE_COUNT = 150
    srt_lines = []
    for i in range(LINE_COUNT):
        start = i * 2
        end = start + 1
        srt_lines.append(
            f"{i + 1}\n{_timestamp(start)} --> {_timestamp(end)}\nLinea {i}.\n"
        )
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    provider = MockProvider()

    batch_calls: list[list[str]] = []

    async def _spy_check_grammar_necessity(pairs, profile):
        batch_calls.append([p["id"] for p in pairs])
        # 모든 줄을 성별 체크 필요로 표시해 segment_resolutions에서 전체
        # 커버리지를 검증할 수 있게 한다.
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False}
                for p in pairs]

    monkeypatch.setattr(provider, "check_grammar_necessity", _spy_check_grammar_necessity)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert len(batch_calls) > 1, "150줄이면 배치 크기 100으로 최소 2번 호출돼야 한다"
    covered_ids = {seg_id for batch in batch_calls for seg_id in batch}
    assert len(covered_ids) == LINE_COUNT, "모든 줄이 어느 한 배치에는 빠짐없이 포함돼야 한다"
    assert len(result["segment_resolutions"]) == LINE_COUNT


@pytest.mark.asyncio
async def test_pipeline_continues_when_check_grammar_necessity_raises(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBAD_TRANSLATION aquí....\n", encoding="utf-8")
    provider = MockProvider()

    async def _raises(pairs, profile):
        raise RuntimeError("문법 판단 API 오류")

    monkeypatch.setattr(provider, "check_grammar_necessity", _raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert result["segment_resolutions"] == []
    assert result["warnings"] == [{"stage": "문법 필요성 판단", "message": "문법 판단 API 오류"}]
    # 나머지 파이프라인은 계속 진행된다 — GPT 2차가 BAD_TRANSLATION 마커를 정상 탐지.
    assert any(f.model == "gpt" for f in result["findings"])


@pytest.mark.asyncio
async def test_pipeline_attaches_english_pronoun_hint_to_gender_flagged_segment(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nEsta cansada.\n", encoding="utf-8")
    english_srt_path = tmp_path / "english.srt"
    english_srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nShe is tired.\n", encoding="utf-8")
    provider = MockProvider()

    async def _flag_gender(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False}
                for p in pairs]

    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_gender)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            english_srt_path=str(english_srt_path),
        )

    resolutions = result["segment_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["english_pronoun_hint"] == {
        "text": "She is tired.", "he_count": 0, "she_count": 1,
    }


@pytest.mark.asyncio
async def test_pipeline_english_pronoun_hint_is_none_without_english_srt_path(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nEsta cansada.\n", encoding="utf-8")
    provider = MockProvider()

    async def _flag_gender(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": True, "formality_check_needed": False}
                for p in pairs]

    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_gender)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert result["segment_resolutions"][0]["english_pronoun_hint"] is None


@pytest.mark.asyncio
async def test_pipeline_does_not_compute_pronoun_hint_for_formality_only_flags(tmp_path, monkeypatch):
    """design §영어 SRT 대조는 "걸린 줄"(성별 체크가 필요한 줄)에 한정된다 —
    격식만 걸린 줄에는 영어 SRT 힌트를 계산하지 않는다(대명사는 성별 신호이지
    격식 신호가 아니다)."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n¿Tú o usted?\n", encoding="utf-8")
    english_srt_path = tmp_path / "english.srt"
    english_srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nShe is tired.\n", encoding="utf-8")
    provider = MockProvider()

    async def _flag_formality(pairs, profile):
        return [{"id": p["id"], "gender_check_needed": False, "formality_check_needed": True}
                for p in pairs]

    monkeypatch.setattr(provider, "check_grammar_necessity", _flag_formality)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            english_srt_path=str(english_srt_path),
        )

    assert result["segment_resolutions"][0]["english_pronoun_hint"] is None


@pytest.mark.asyncio
async def test_pipeline_transcribes_in_chunks_and_merges_with_correct_offsets(tmp_path, monkeypatch):
    """STT 오디오가 여러 조각으로 나뉘면, 각 조각을 병렬로 transcribe하고
    조각 인덱스 * chunk_seconds만큼 타임코드를 보정해서 순서대로 이어붙여야
    한다 (design: asyncio.gather는 완료 순서와 무관하게 입력 순서로 결과를
    반환하므로, 병렬로 돌려도 최종 순서가 흐트러지지 않는다)."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()

    async def _fake_transcribe(audio_path):
        # 조각 경로별로 다른 세그먼트를 돌려줘서, 병합 결과의 순서/오프셋을
        # 검증할 수 있게 한다. 두 조각 다 자기 파일 안에서는 0초부터
        # 시작하는 세그먼트를 반환한다(진짜 STT가 조각 파일 하나를 새
        # 오디오로 보고 처리하는 것과 동일).
        if audio_path == "/fake/chunk0.wav":
            return [{"start": 0.0, "end": 1.0, "text": "첫 조각"}]
        if audio_path == "/fake/chunk1.wav":
            return [{"start": 0.0, "end": 1.5, "text": "둘째 조각"}]
        raise AssertionError(f"예상치 못한 조각 경로: {audio_path}")

    monkeypatch.setattr(provider, "transcribe", _fake_transcribe)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks",
               return_value=["/fake/chunk0.wav", "/fake/chunk1.wav"]), \
         patch("app.core.pipeline.STT_CHUNK_SECONDS", 600.0):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    raw = result["korean_segments_raw"]
    assert len(raw) == 2
    assert raw[0]["text"] == "첫 조각"
    assert raw[0]["start"] == 0.0
    assert raw[0]["end"] == 1.0
    assert raw[1]["text"] == "둘째 조각"
    # 둘째 조각은 600초(STT_CHUNK_SECONDS) 오프셋이 더해져야 한다.
    assert raw[1]["start"] == 600.0
    assert raw[1]["end"] == 601.5


@pytest.mark.asyncio
async def test_pipeline_tolerates_a_speech_free_chunk_among_others(tmp_path, monkeypatch):
    """회귀(Finding #1): 조각 하나에 세그먼트가 없는 것(무음 구간, 엔드크레딧
    등)은 그 자체로는 실패가 아니다 — 나머지 조각의 세그먼트만으로 병합
    결과가 채워지면 파이프라인은 정상적으로 끝나야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()

    async def _fake_transcribe(audio_path):
        if audio_path == "/fake/chunk0.wav":
            return []
        return [{"start": 0.0, "end": 1.0, "text": "대사"}]

    monkeypatch.setattr(provider, "transcribe", _fake_transcribe)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks",
               return_value=["/fake/chunk0.wav", "/fake/chunk1.wav"]):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert len(result["korean_segments_raw"]) == 1
    assert result["korean_segments_raw"][0]["text"] == "대사"


@pytest.mark.asyncio
async def test_pipeline_raises_when_all_chunks_have_no_speech(tmp_path, monkeypatch):
    """회귀(Finding #1): 모든 조각을 병합한 결과가 통째로 비어 있다면 —
    에피소드 전체에 대사가 없다는 뜻이므로 — 여전히 진짜 실패로 처리해야
    한다. 이 판단은 이제 조각 단위가 아니라 병합 후(_transcribe_in_chunks)
    한 번만 이뤄진다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()

    async def _empty_transcribe(audio_path):
        return []

    monkeypatch.setattr(provider, "transcribe", _empty_transcribe)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks",
               return_value=["/fake/chunk0.wav", "/fake/chunk1.wav"]):
        with pytest.raises(ValueError, match="GPT STT 응답에 세그먼트가 없음"):
            await run_pipeline(
                video_path="/fake/video.mp4", target_srt_path=str(srt_path),
                language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            )


@pytest.mark.asyncio
async def test_pipeline_deletes_original_wav_early_when_split_actually_occurred(tmp_path, monkeypatch):
    """회귀(Finding #3): 분할이 실제로 일어났다면(청크가 원본을 완전히
    대체) _transcribe_in_chunks가 병렬 transcribe를 시작하기 전에 원본
    wav_path를 바로 지워야 한다 — 그래야 STT 진행 중 오디오 관련 디스크
    사용량이 원본+조각으로 일시적으로 두 배가 되는 걸 막을 수 있다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()
    wav_path = tmp_path / "audio.wav"
    wav_path.write_bytes(b"fake wav")
    chunk0 = tmp_path / "chunk0.wav"
    chunk1 = tmp_path / "chunk1.wav"
    chunk0.write_bytes(b"chunk0")
    chunk1.write_bytes(b"chunk1")

    wav_existed_during_transcribe = []

    async def _fake_transcribe(audio_path):
        wav_existed_during_transcribe.append(wav_path.exists())
        return [{"start": 0.0, "end": 1.0, "text": "조각"}]

    monkeypatch.setattr(provider, "transcribe", _fake_transcribe)

    with patch("app.core.pipeline.extract_audio", return_value=str(wav_path)), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks",
               return_value=[str(chunk0), str(chunk1)]):
        await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    # 원본은 병렬 transcribe가 시작되기 전에(즉 그 동안 내내) 이미 없어야 한다.
    assert wav_existed_during_transcribe == [False, False]
    assert not wav_path.exists()


@pytest.mark.asyncio
async def test_pipeline_does_not_delete_original_wav_early_when_no_split_needed(tmp_path, monkeypatch):
    """회귀(Finding #3): 분할이 일어나지 않았다면(원본이 이미 청크 길이보다
    짧음) _transcribe_in_chunks는 원본을 손대면 안 된다 — run_pipeline의
    바깥 finally 블록이 정확히 한 번 정리하는 게 유일한 소유권이어야
    한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()
    wav_path = tmp_path / "audio.wav"
    wav_path.write_bytes(b"fake wav")

    wav_existed_during_transcribe = []

    async def _fake_transcribe(audio_path):
        wav_existed_during_transcribe.append(wav_path.exists())
        return [{"start": 0.0, "end": 1.0, "text": "조각"}]

    monkeypatch.setattr(provider, "transcribe", _fake_transcribe)

    with patch("app.core.pipeline.extract_audio", return_value=str(wav_path)), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks", return_value=[str(wav_path)]):
        await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert wav_existed_during_transcribe == [True]
    # run_pipeline의 바깥 finally 블록이 정확히 한 번 지운다.
    assert not wav_path.exists()


@pytest.mark.asyncio
async def test_pipeline_cleans_up_chunk_files_but_not_original_when_no_split_needed(tmp_path, monkeypatch):
    """split_audio_into_chunks가 분할 없이 원본 경로를 그대로 반환했을 때
    (짧은 오디오), _transcribe_in_chunks가 그 경로를 지우면 안 된다 —
    run_pipeline의 기존 finally 블록이 wav_path 자체를 이미 정리하므로,
    여기서 또 지우면 안전하긴 하지만(unlink는 missing_ok) 원본이 아직
    필요한 시점(예: 캐싱 재사용)에 조기 삭제될 위험을 열어두는 셈이라
    개념적으로 원본은 절대 손대면 안 된다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola.\n", encoding="utf-8")
    provider = MockProvider()
    fake_wav = "/fake/audio.wav"

    unlinked_paths = []
    original_unlink = Path.unlink

    def _spy_unlink(self, missing_ok=False):
        unlinked_paths.append(str(self))
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _spy_unlink)

    with patch("app.core.pipeline.extract_audio", return_value=fake_wav), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.split_audio_into_chunks", return_value=[fake_wav]):
        await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    # wav_path 자체는 run_pipeline의 기존 finally 블록이 한 번 지운다 — 그건
    # 정상. 여기서 확인하려는 건 _transcribe_in_chunks가 "추가로 또" 같은
    # 경로를 조각 정리 명목으로 지우려 하지 않는지다. fake_wav 경로가 두 번
    # 이상 unlink 호출에 나타나면 안 된다.
    assert unlinked_paths.count(fake_wav) <= 1
