from pathlib import Path

import pytest
from unittest.mock import patch
from app.core.pipeline import run_pipeline, run_pipeline_phase1, _median_offset_from_stt_matches
from app.providers.mock import MockProvider
from app.schemas import SegmentText

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

    assert any(f.category == "mistranslation" and f.model == "claude+gpt" for f in result["findings"])
    assert any(v.rule == "ellipsis" for v in result["format_violations"])
    assert result["video_proxy_path"] == "/fake/proxy.mp4"
    assert "pairs" in result


@pytest.mark.asyncio
async def test_pipeline_runs_claude_and_gpt_on_the_same_original_text(tmp_path, monkeypatch):
    """병렬 독립 검증의 핵심 계약: Claude와 GPT는 같은 원본을 동시에 받아야
    하고, 어느 쪽도 상대가 뭘 고쳤는지/봤는지 모른 채 판단해야 한다(앵커링
    편향 방지 — design §어떻게 사용하는지). 둘 다 받는 target_text가 서로
    같고, 사전필터까지만 적용된 원본이어야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    captured = {}

    async def _capture_claude(pairs, *args, **kwargs):
        captured["claude_saw"] = pairs[0]["target_text"]
        return []

    async def _capture_gpt(pairs, *args, **kwargs):
        captured["gpt_saw"] = pairs[0]["target_text"]
        return []

    monkeypatch.setattr(provider, "correct_primary", _capture_claude)
    monkeypatch.setattr(provider, "verify_and_refine", _capture_gpt)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    # MockProvider의 STT가 반환하는 "안녕하세요"(존댓말)가 격식 자동 확정으로
    # 이어져, S2가 보기 전에 이미 "[formal] " 마커가 target_text에 반영돼
    # 있다(design §AI에게 반영해달라 부탁하지 말고 먼저 확정) — 이 테스트의
    # 핵심 계약(둘이 같은 입력을 받는다)은 여전히 유지된다.
    assert captured["claude_saw"] == captured["gpt_saw"] == "[formal] BAD_TRANSLATION aquí..."


@pytest.mark.asyncio
async def test_pipeline_auto_applies_when_claude_and_gpt_agree_and_confirm_equivalence(
        tmp_path, monkeypatch):
    """같은 줄을 Claude/GPT 둘 다 지적했고(합의 후보), 문구가 달라도 둘 다
    "같은 뜻"이라고 교차 확인해주면(진짜 합의) 사람 승인 없이 자동 적용된다
    — 스페인어를 모르는 검수자는 텍스트 품질을 판단할 수 없으므로, 이 교차
    확인이 유일한 신뢰도 신호다. 문구는 고정 규칙으로 GPT 쪽을 쓴다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de claude", "description": "클로드 제안"}]

    async def _gpt_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de gpt", "description": "지피티 제안"}]

    async def _confirms_equivalent(items, profile):
        return [{"id": i["id"], "equivalent": True} for i in items]

    monkeypatch.setattr(provider, "correct_primary", _claude_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _gpt_flags)
    monkeypatch.setattr(provider, "check_equivalence_with_claude", _confirms_equivalent)
    monkeypatch.setattr(provider, "check_equivalence_with_gpt", _confirms_equivalent)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    finding = next(f for f in result["findings"] if f.model == "claude+gpt")
    assert finding.status == "approved"
    assert finding.suggested_text == "texto de gpt"


@pytest.mark.asyncio
async def test_pipeline_creates_two_pending_findings_when_equivalence_check_disagrees(
        tmp_path, monkeypatch):
    """합의 후보였지만(둘 다 같은 줄 지적) 문구가 실질적으로 다르다고 어느
    한쪽이라도 판정하면 진짜 합의가 아니다 — 자동 적용하지 않고 Claude/GPT
    제안을 각각 별도의 pending Finding으로 남긴다(기존 "한쪽만 지적" 처리와
    동일)."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de claude", "description": "클로드 제안"}]

    async def _gpt_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de gpt", "description": "지피티 제안"}]

    async def _claude_says_different(items, profile):
        return [{"id": i["id"], "equivalent": False} for i in items]

    async def _gpt_says_equivalent(items, profile):
        return [{"id": i["id"], "equivalent": True} for i in items]

    monkeypatch.setattr(provider, "correct_primary", _claude_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _gpt_flags)
    # 하나라도 "다르다"고 하면 불일치로 처리돼야 함을 확인하기 위해 일부러
    # 두 판정을 다르게 둔다.
    monkeypatch.setattr(provider, "check_equivalence_with_claude", _claude_says_different)
    monkeypatch.setattr(provider, "check_equivalence_with_gpt", _gpt_says_equivalent)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    assert not any(f.model == "claude+gpt" for f in result["findings"])
    claude_finding = next(f for f in result["findings"] if f.model == "claude")
    gpt_finding = next(f for f in result["findings"] if f.model == "gpt")
    assert claude_finding.status == "pending" and claude_finding.suggested_text == "texto de claude"
    assert gpt_finding.status == "pending" and gpt_finding.suggested_text == "texto de gpt"
    final_pair = next(p for p in result["pairs"] if p.target is not None)
    # MockProvider의 STT가 반환하는 "안녕하세요"(존댓말)가 격식 자동 확정으로
    # 이어져, S2 이전에 이미 "[formal] " 마커가 반영된다 — 이 테스트의 핵심
    # 계약(불일치 시 교정을 적용하지 않고 원문을 유지)은 여전히 유지된다.
    assert final_pair.target.text == "[formal] BAD_TRANSLATION aquí..."


@pytest.mark.asyncio
async def test_pipeline_does_not_apply_when_only_one_model_flags_a_segment(tmp_path, monkeypatch):
    """한쪽만 지적하면(불일치) 적용하지 않고 원문을 유지한다 — 사람이
    스페인어 품질을 판단할 수 없으므로, 확신 없는 교정을 조용히 적용하는
    건 위험하다. 대신 pending Finding으로 남겨 감사할 수 있게 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de claude", "description": "클로드만 지적"}]

    async def _gpt_no_flags(pairs, *args, **kwargs):
        return []

    monkeypatch.setattr(provider, "correct_primary", _claude_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _gpt_no_flags)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    finding = next(f for f in result["findings"] if f.model == "claude")
    assert finding.status == "pending"
    assert finding.final_text == ""
    final_pair = next(p for p in result["pairs"] if p.target is not None)
    # MockProvider의 STT가 반환하는 "안녕하세요"(존댓말)가 격식 자동 확정으로
    # 이어져, S2 이전에 이미 "[formal] " 마커가 반영된다 — 이 테스트의 핵심
    # 계약(불일치 시 교정을 적용하지 않고 원문을 유지)은 여전히 유지된다.
    assert final_pair.target.text == "[formal] BAD_TRANSLATION aquí..."


@pytest.mark.asyncio
async def test_pipeline_attaches_cross_model_backtranslation_to_disputed_finding(tmp_path, monkeypatch):
    """의견이 갈린 제안은 반대쪽 모델이 역번역한 한국어를 description에
    참고용으로 붙여야 한다 — 스페인어를 모르는 검수자가 최소한 의미가
    통하는지는 가늠할 수 있게."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de claude", "description": "클로드만 지적"}]

    async def _gpt_no_flags(pairs, *args, **kwargs):
        return []

    async def _gpt_back_translates(texts, profile):
        return [{"id": t["id"], "korean_text": f"역번역됨:{t['text']}"} for t in texts]

    monkeypatch.setattr(provider, "correct_primary", _claude_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _gpt_no_flags)
    monkeypatch.setattr(provider, "back_translate_with_gpt", _gpt_back_translates)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    finding = next(f for f in result["findings"] if f.model == "claude")
    assert "역번역됨:texto de claude" in finding.description


@pytest.mark.asyncio
async def test_pipeline_keeps_backtranslation_separate_per_model_on_disputed_segment(
    tmp_path, monkeypatch,
):
    """회귀: 같은 줄을 두 모델이 서로 다른 문구로 지적하면(의견 갈림),
    각자의 역번역이 섞이거나 한쪽이 다른 쪽 걸 덮어써서는 안 된다 —
    segment_id만으로 역번역을 키하면 disputed 케이스에서 한쪽 역번역이
    사라지고 남은 값이 양쪽 finding에 똑같이 붙는 버그가 있었다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    provider = MockProvider()

    async def _claude_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de claude", "description": "클로드 지적"}]

    async def _gpt_flags(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "texto de gpt", "description": "GPT 지적"}]

    async def _gpt_back_translates(texts, profile):
        # Claude 문구를 GPT가 역번역
        return [{"id": t["id"], "korean_text": f"클로드문구역번역:{t['text']}"} for t in texts]

    async def _claude_back_translates(texts, profile):
        # GPT 문구를 Claude가 역번역
        return [{"id": t["id"], "korean_text": f"GPT문구역번역:{t['text']}"} for t in texts]

    monkeypatch.setattr(provider, "correct_primary", _claude_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _gpt_flags)
    monkeypatch.setattr(provider, "back_translate_with_gpt", _gpt_back_translates)
    monkeypatch.setattr(provider, "back_translate_with_claude", _claude_back_translates)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=provider,
        )

    claude_finding = next(f for f in result["findings"] if f.model == "claude")
    gpt_finding = next(f for f in result["findings"] if f.model == "gpt")
    assert "클로드문구역번역:texto de claude" in claude_finding.description
    assert "GPT문구역번역:texto de gpt" in gpt_finding.description


@pytest.mark.asyncio
async def test_back_translate_proposals_chunks_large_batches():
    """회귀(사용자 재현: "역번역이 아예 안 뜨거나 이상하게 나옴"): 영화
    전체 분량을 한 콜에 몰아넣으면 응답이 토큰 한도에서 잘려 JSON 파싱이
    통째로 실패하거나 id가 엉뚱한 항목에 붙는다 — _verify_chunk가 이미
    같은 이유로 씬 단위 청킹을 쓰는데, 역번역 콜에는 그 보호가 빠져 있었다.
    CHUNK_MAX_SIZE를 넘는 분량이면 여러 번에 나눠 호출돼야 한다."""
    from app.core.pipeline import _back_translate_proposals, CHUNK_MAX_SIZE

    call_batches = []

    async def _gpt_back_translates(texts, profile):
        call_batches.append(len(texts))
        return [{"id": t["id"], "korean_text": f"역번역:{t['text']}"} for t in texts]

    class _FakeProvider:
        back_translate_with_gpt = staticmethod(_gpt_back_translates)

        @staticmethod
        async def back_translate_with_claude(texts, profile):
            return []

    total = CHUNK_MAX_SIZE + 5  # 한 청크로는 안 들어가는 양
    claude_only = [{"segment_id": f"p{i}", "corrected_text": f"texto {i}"} for i in range(total)]

    backtranslation_by_id, original_backtranslation_by_id, not_improved, warnings = await _back_translate_proposals(
        _FakeProvider(), {"language": "es"}, agreed=[], claude_only=claude_only, gpt_only=[],
        target_version_id="tv1", pairs=[],
    )

    assert warnings == []
    assert not_improved == set()  # is_improvement 없이 응답 → 기본 보존
    assert len(call_batches) == 2  # 35 + 5, 한 콜에 다 안 들어가고 두 번 나뉨
    assert all(n <= CHUNK_MAX_SIZE for n in call_batches)
    assert len(backtranslation_by_id) == total
    assert backtranslation_by_id[("p0", "claude_authored")] == "역번역:texto 0"
    assert backtranslation_by_id[(f"p{total - 1}", "claude_authored")] == f"역번역:texto {total - 1}"


@pytest.mark.asyncio
async def test_back_translate_proposals_survives_one_chunk_failing():
    """회귀: 청크 하나가 실패해도(네트워크 오류 등) 그 청크만 역번역 없이
    넘어가고, 나머지 청크의 역번역은 살아남아야 한다 — 예전처럼 전체를
    한 콜로 보내면 하나 실패할 때 전부 사라졌다."""
    from app.core.pipeline import _back_translate_proposals, CHUNK_MAX_SIZE

    async def _gpt_back_translates(texts, profile):
        if texts[0]["id"] == "p0":
            raise RuntimeError("네트워크 오류")
        return [{"id": t["id"], "korean_text": f"역번역:{t['text']}"} for t in texts]

    class _FakeProvider:
        back_translate_with_gpt = staticmethod(_gpt_back_translates)

        @staticmethod
        async def back_translate_with_claude(texts, profile):
            return []

    total = CHUNK_MAX_SIZE + 5
    claude_only = [{"segment_id": f"p{i}", "corrected_text": f"texto {i}"} for i in range(total)]

    backtranslation_by_id, original_backtranslation_by_id, not_improved, warnings = await _back_translate_proposals(
        _FakeProvider(), {"language": "es"}, agreed=[], claude_only=claude_only, gpt_only=[],
        target_version_id="tv1", pairs=[],
    )

    assert len(warnings) == 1  # 첫 청크만 실패
    assert ("p0", "claude_authored") not in backtranslation_by_id
    # 두 번째 청크(실패한 청크 밖)는 살아남는다.
    assert backtranslation_by_id[(f"p{total - 1}", "claude_authored")] == f"역번역:texto {total - 1}"


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

    async def _introduces_ellipsis(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "espera......", "description": "GPT가 늘어뜨림"}]

    # Claude/GPT 둘 다 같은 문구를 내야 MockProvider의 기본 동등성 판정
    # (text_a == text_b)이 true가 되어 진짜 합의로 확정되고 실제로 적용된다.
    monkeypatch.setattr(provider, "correct_primary", _introduces_ellipsis)
    monkeypatch.setattr(provider, "verify_and_refine", _introduces_ellipsis)

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
async def test_pipeline_verification_pairs_reflect_post_pretreatment_text(
        tmp_path, monkeypatch):
    """Claude/GPT가 검증하는 target_text는 사전필터(#3/#4/#6, 정책적 편집)까지
    적용된 뒤의 텍스트여야 한다 — 사전필터 이전 원본을 보여주면 이미
    정책적으로 치환/삭제된 내용을 다시 "복원"하도록 유도될 수 있다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBAD_TRANSLATION mierda....\n", encoding="utf-8")
    provider = MockProvider()

    captured = {}

    async def _capture_verify_and_refine(pairs, *args, **kwargs):
        captured["target_text"] = pairs[0]["target_text"]
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

    assert "mierda" not in captured["target_text"]
    assert "[삐-]" in captured["target_text"]


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
async def test_pipeline_uses_embedding_dp_when_korean_srt_path_given(tmp_path):
    """korean_srt_path가 주어지면 대사 내용/타이밍은 STT가 아니라 OpenAI
    text-embedding-3-small + DP 알고리즘으로 한국어 SRT와 대상언어 SRT를 직접
    정렬한다 — 결과 텍스트와 타이밍은 한국어 SRT 큐([5.0, 7.0])의 값을 그대로
    쓴다. 다만 extract_audio는 여전히(내용용이 아니라) 영상 재생 동기화
    오프셋 탐지용으로 앞부분 클립만 짧게 호출된다(design §2026-08 영상
    동기화 버그 수정). 원본이 아니라 방금 만든 프록시 경로로 호출된다 —
    원본은 나중에 스토리지 정리로 지워질 수 있어 의존하면 안 된다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:05,000 --> 00:00:07,000\n안녕하세요!\n", encoding="utf-8",
    )

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
            korean_srt_path=str(ko_srt_path),
        )

    # 영상 동기화용으로만 짧게(첫 큐 끝 + 여유분) 호출된다 — 내용용 전체
    # STT는 여전히 안 돈다. 원본(/fake/video.mp4)이 아니라 프록시 경로로
    # 호출돼야 한다.
    mock_extract.assert_called_once_with("/fake/proxy.mp4", duration_seconds=pytest.approx(97.0))
    korean_pair = next(p for p in result["pairs"] if p.korean is not None)
    assert korean_pair.korean.text == "안녕하세요!"
    assert korean_pair.korean.start == 5.0
    assert korean_pair.korean.end == 7.0


@pytest.mark.asyncio
async def test_pipeline_drops_pure_stage_direction_korean_cue(tmp_path):
    """회귀: 한국어 SRT 큐가 지문/효과음뿐이라 정제 후 대사가 하나도 안
    남으면(예: "[침을 퉤퉤 뱉는다] [웃음]"), 브라켓 원본을 그대로 되살려
    노출하면 안 된다 — 그런 큐는 버려야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n[침을 퉤퉤 뱉는다] [웃음]\n\n"
        "2\n00:00:01,500 --> 00:00:03,500\n안녕하세요!\n",
        encoding="utf-8",
    )

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
            korean_srt_path=str(ko_srt_path),
        )

    korean_texts = [p.korean.text for p in result["pairs"] if p.korean is not None]
    assert not any("[" in t for t in korean_texts)


@pytest.mark.asyncio
async def test_pipeline_auto_corrects_constant_offset_with_korean_srt_path(tmp_path):
    """회귀: korean_srt_path 경로는 대사 정렬용으로는 STT를 안 거치므로, 한국어
    SRT와 대상언어 SRT가 각자 적어놓은 타임코드끼리 비교한 오프셋(정렬용,
    global_offset)을 찾아 짝짓기에 반영한다 — 이건 실제 영상 파일과는
    무관하다. 영상 재생 동기화용 video_offset_seconds는 별도로, 앞부분을
    짧게 STT해서 실측 오디오 기준(한국어 SRT 원본 시계 기준)으로 raw
    오프셋을 구한 뒤, Segment.start가 실제로 쓰는 시계(대상언어 SRT
    시계 ≈ 한국어 SRT 원본 + global_offset)에 맞춰 global_offset과
    합산한다(design §2026-08 영상 동기화 버그 수정) — 이전엔 이 둘을 같은
    값으로 취급하거나(1차 버그) STT 원시값을 합산 없이 그대로 썼다(2차
    버그, 기준 시계가 안 맞음). 두 오프셋이 서로 다른 값이어야 두
    메커니즘이 섞이지 않고 각자 검증된다."""
    from app.core.ingest import build_srt

    starts = [0.0, 13.0, 41.0, 68.0, 100.0, 155.0]
    # 대상언어 SRT는 한국어 SRT보다 55초 늦게(뒤로 밀려) 찍혀 있다 — 이건
    # 정렬(global_offset)용 오프셋이다.
    target_entries = [
        {"start": s + 55.0, "end": s + 55.0 + 3.0, "text": f"Linea {i}"}
        for i, s in enumerate(starts)
    ]
    target_srt_path = tmp_path / "target.srt"
    target_srt_path.write_text(build_srt(target_entries), encoding="utf-8")

    korean_entries = [
        {"start": s, "end": s + 3.0, "text": f"대사 {i}"} for i, s in enumerate(starts)
    ]
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(build_srt(korean_entries), encoding="utf-8")

    # 실제 영상의 진짜 오디오는 한국어 SRT 표기 시각보다 20초 늦게 나온다고
    # 가정한다 — 정렬용 오프셋(55.0)과는 다른 값이어야 두 메커니즘이 서로
    # 안 섞이고 각자 검증된다.
    VIDEO_OFFSET = 20.0

    class SyncedProvider(MockProvider):
        async def transcribe(self, audio_path):
            words = []
            for i, s in enumerate(starts[:5]):
                t = s + VIDEO_OFFSET
                words.append({"start": t, "end": t + 0.4, "text": "대사"})
                words.append({"start": t + 0.4, "end": t + 0.8, "text": str(i)})
            return words

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(target_srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=SyncedProvider(),
            korean_srt_path=str(ko_srt_path),
        )

    # video_offset_seconds = global_offset(대상언어 SRT vs 한국어 SRT 원본,
    # ≈55.0이지만 1초 단위 coarse step 때문에 근사값) - raw STT 오프셋(20.0).
    # 정확히 20.0이 아니라 대략 35.0 근방이어야 두 메커니즘이 합쳐졌다는
    # 뜻이다.
    assert result["video_offset_seconds"] == pytest.approx(55.0 - VIDEO_OFFSET, abs=1.5)
    assert any(w["stage"] == "타임코드 자동 보정" for w in result["warnings"])
    matched = {p.target.text: p.korean.text for p in result["pairs"] if p.target and p.korean}
    assert matched["Linea 0"] == "대사 0"
    assert matched["Linea 3"] == "대사 3"


def test_median_offset_from_stt_matches_ignores_cues_beyond_limit_and_outliers():
    raw_cues = [
        SegmentText(start=0.0, end=1.0, text="a"),
        SegmentText(start=10.0, end=11.0, text="b"),
        SegmentText(start=20.0, end=21.0, text="c"),
        SegmentText(start=999.0, end=1000.0, text="far away, outside anchor window"),
    ]
    matched_words = [
        {"cue_index": 0, "start": 20.0, "end": 20.5, "text": "a", "confirmed": True},   # +20
        {"cue_index": 1, "start": 30.5, "end": 31.0, "text": "b", "confirmed": True},   # +20.5
        {"cue_index": 2, "start": 61.0, "end": 61.5, "text": "c", "confirmed": True},   # +41 (outlier)
        # cue_index 3은 num_cues(=3) 범위 밖이라 무시돼야 한다.
        {"cue_index": 3, "start": 5000.0, "end": 5000.5, "text": "z", "confirmed": True},
    ]
    offset = _median_offset_from_stt_matches(matched_words, raw_cues, num_cues=3)
    assert offset == pytest.approx(20.5)


def test_median_offset_from_stt_matches_returns_none_when_no_matches_within_limit():
    raw_cues = [SegmentText(start=0.0, end=1.0, text="a")]
    matched_words = [{"cue_index": 5, "start": 100.0, "end": 100.5, "text": "far", "confirmed": True}]
    assert _median_offset_from_stt_matches(matched_words, raw_cues, num_cues=3) is None


def test_median_offset_from_stt_matches_ignores_unconfirmed_interpolated_words():
    """회귀: 고유명사(인명·마스코트 이름 등)를 STT가 잘못 들어 확정을 못
    하면 match_stt_words_to_korean_srt가 confirmed=False인 추정값을
    돌려준다 — 이걸 실측처럼 오프셋 계산에 넣으면 안 된다."""
    raw_cues = [
        SegmentText(start=0.0, end=1.0, text="a"),
        SegmentText(start=10.0, end=11.0, text="b"),
    ]
    matched_words = [
        {"cue_index": 0, "start": 20.0, "end": 20.5, "text": "a", "confirmed": True},
        # cue 1은 보간(추정)값뿐이라 근거에서 빠져야 한다 — 이게 안 빠지면
        # 아래 기대값(20.0)이 아니라 둘의 중앙값이 나온다.
        {"cue_index": 1, "start": 999.0, "end": 999.5, "text": "b", "confirmed": False},
    ]
    offset = _median_offset_from_stt_matches(matched_words, raw_cues, num_cues=2)
    assert offset == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_pipeline_reports_real_video_offset_with_korean_srt_path(tmp_path):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요\n", encoding="utf-8",
    )

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
            korean_srt_path=str(ko_srt_path),
        )

    assert result["video_offset_seconds"] == 0.0



@pytest.mark.asyncio
async def test_pipeline_auto_corrects_constant_offset_between_stt_and_srt(tmp_path):
    """회귀: 영상 앞부분을 잘라 올려(리캡/인트로 제거 등) 한국어 STT
    타임코드 전체가 대상언어 SRT보다 상수만큼 앞서 있어도, 파이프라인이
    자동으로 오프셋을 찾아 보정해서 올바른 큐에 한국어 원문이 붙어야
    한다."""
    from app.core.ingest import build_srt

    entries = [
        {"start": start, "end": start + 5.0, "text": f"Linea {i}"}
        for i, start in enumerate([0.0, 13.0, 41.0, 68.0, 100.0, 155.0])
    ]
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(build_srt(entries), encoding="utf-8")

    # 실제 STT 타임코드는 55초 앞서 있다(영상 앞을 55초 잘라 올린 상황).
    cached_words = [
        {"start": e["start"] - 55.0 + 1.0, "end": e["start"] - 55.0 + 1.5, "text": f"단어{i}"}
        for i, e in enumerate(entries)
    ]

    result = await run_pipeline(
        video_path="/fake/video.mp4", target_srt_path=str(srt_path),
        language="es", variant="LATAM", target_version_id="tv1", provider=MockProvider(),
        cached_korean_segments=cached_words, cached_video_proxy_path="/fake/cached_proxy.mp4",
    )

    matched = {p.target.text: p.korean.text for p in result["pairs"] if p.target and p.korean}
    assert matched["Linea 0"] == "단어0"
    assert matched["Linea 3"] == "단어3"
    assert any(w["stage"] == "타임코드 자동 보정" for w in result["warnings"])


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

    def _flag_first_only(pairs, profile):
        return [
            {"id": p["id"], "gender_check_needed": p["id"] == "pair_1",
             "formality_check_needed": False, "resolved_formality": None,
             "resolved_gender_from_korean": None,
             "candidate_words": [], "candidate_word_lemmas": []}
            for p in pairs
        ]

    monkeypatch.setattr("app.core.pipeline.check_grammar_necessity", _flag_first_only)

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
async def test_pipeline_continues_when_check_grammar_necessity_raises(tmp_path, monkeypatch):
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nBAD_TRANSLATION aquí....\n", encoding="utf-8")
    provider = MockProvider()

    def _raises(pairs, profile):
        raise RuntimeError("문법 판단 API 오류")

    monkeypatch.setattr("app.core.pipeline.check_grammar_necessity", _raises)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert result["segment_resolutions"] == []
    assert result["warnings"] == [{"stage": "문법 필요성 판단", "message": "문법 판단 API 오류"}]
    # 나머지 파이프라인은 계속 진행된다 — Claude/GPT 둘 다 BAD_TRANSLATION 마커를
    # 정상 탐지하고 합의하므로 자동 적용된다.
    assert any(f.model == "claude+gpt" for f in result["findings"])


@pytest.mark.asyncio
async def test_pipeline_auto_resolves_formality_from_korean_ending_without_stepper(tmp_path):
    """한국어 어미로 격식이 자동 판정되면 segment_resolutions에
    resolved_formality가 바로 채워져야 한다 — 검수자가 스테퍼에서 또 물을
    필요가 없다(design §정말 판단하기 어려운 것만 질문)."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n¿Puede venir?\n", encoding="utf-8")

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1",
            provider=MockProvider(),
        )

    resolution = result["segment_resolutions"][0]
    assert resolution["formality_check_needed"] is True
    assert resolution["resolved_formality"] == "formal"


@pytest.mark.asyncio
async def test_pipeline_applies_resolved_formality_before_dual_verification(tmp_path, monkeypatch):
    """한국어로 미리 확정된 격식은 Claude/GPT 검증(S2)이 보기 전에 전담 LLM
    호출(apply_formality)로 이미 문장에 반영돼 있어야 한다 — "반영해달라고
    부탁"하는 대신 먼저 확정해서 넘긴다(design §AI에게 반영해달라 부탁하지
    말고 파이썬/전담 호출이 먼저 확정). MockProvider.apply_formality는
    "[formality] " 접두어를 붙이는 결정론적 마커라, 이 접두어가 S2로 넘어가는
    target_text에 이미 붙어 있으면 성공이다."""
    srt_path = tmp_path / "target.srt"
    # MockProvider의 STT는 "안녕하세요"(존댓말 어미)를 반환한다. 성별 표시
    # 후보 단어가 없는 문장을 써서(¿Cómo estás?에는 성별 어미 형용사가 없음)
    # 격식 확정 경로만 단독으로 검증한다 — 성별 후보가 있었다면 LLM 그룹핑을
    # 거치며 미확정 상태로 남아 _build_resolved_registers가 이 줄 전체(격식
    # 포함)를 건너뛰어 이 테스트의 의도(격식만 검증)와 무관한 이유로 실패한다.
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n¿Cómo estás?\n", encoding="utf-8")
    provider = MockProvider()

    captured = {}

    async def _capture_correct_primary(pairs, *args, **kwargs):
        captured["pairs"] = pairs
        return []

    monkeypatch.setattr(provider, "correct_primary", _capture_correct_primary)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        )

    assert captured["pairs"][0]["target_text"] == "[formal] ¿Cómo estás?"


@pytest.mark.asyncio
async def test_pipeline_applies_resolved_gender_and_formality_before_dual_verification(
        tmp_path, monkeypatch):
    """회귀: 확정된 성별은 파이썬이 결정론적으로(어미 변형), 확정된 격식은
    전담 LLM 호출로, 둘 다 Claude/GPT 검증(S2)이 pairs를 보기 전에 이미
    target_text에 반영돼 있어야 한다. 입력 "Está cansado."는 여성형
    "cansada"였다가 성별 교정으로 남성형이 되고, 그 위에 격식(반말) 마커가
    덧붙는다 — 순서(성별 먼저, 그다음 격식)까지 같이 확인한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nEstá cansada.\n", encoding="utf-8")
    provider = MockProvider()

    captured = {}

    async def _capture_correct_primary(pairs, *args, **kwargs):
        captured["pairs"] = pairs
        return []

    monkeypatch.setattr(provider, "correct_primary", _capture_correct_primary)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        await run_pipeline(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            cached_korean_segments=[{"start": 0.0, "end": 2.0, "text": "오빠 왜 그래"}],
            cached_video_proxy_path="/fake/proxy.mp4",
        )

    assert captured["pairs"][0]["target_text"] == "[informal] Está cansado."


def test_resolved_registers_treat_not_applicable_gender_as_no_gender_info():
    """회귀: 검수자가 "해당 없음(사람 아님)"을 고르면(예: caro=비싸다,
    사람 성별과 무관) resolved_gender_raw에 "not_applicable"이 저장되는데,
    이 값이 진짜 성별인 것처럼 AI 프롬프트에 전달되면 안 된다 — 격식 정보는
    영향받지 않아야 한다."""
    from app.core.pipeline import _build_resolved_registers, resolved_registers_from_segments

    segment_resolutions = [
        {"segment_id": "pair_1", "resolved_gender": "not_applicable", "resolved_formality": "formal"},
        {"segment_id": "pair_2", "resolved_gender": "male", "resolved_formality": None},
        {"segment_id": "pair_3", "resolved_gender": "not_applicable", "resolved_formality": None},
    ]
    registers = _build_resolved_registers(segment_resolutions)
    assert registers["pair_1"] == {"gender": None, "formality": "formal"}
    assert registers["pair_2"] == {"gender": "male", "formality": None}
    assert "pair_3" not in registers  # 성별도 격식도 실제 정보가 없으면 아예 빠짐

    class _FakeSegment:
        def __init__(self, id, resolved_gender_raw, resolved_formality_raw):
            self.id = id
            self.resolved_gender_raw = resolved_gender_raw
            self.resolved_formality_raw = resolved_formality_raw
            self.resolved_gender_groups_raw = None

    segments = [
        _FakeSegment("tv1:pair_1", "not_applicable", "formal"),
        _FakeSegment("tv1:pair_2", "male", None),
    ]
    registers2 = resolved_registers_from_segments(segments, "tv1")
    assert registers2["pair_1"] == {"gender": None, "formality": "formal"}
    assert registers2["pair_2"] == {"gender": "male", "formality": None}


def test_gender_groups_for_ai_handles_legacy_rows_without_candidate_indices():
    """회귀: 이 브랜치 이전에 저장된 resolved_gender_groups_raw 행은
    candidate_indices 키가 없다(words/target_word_lemmas/gender만 있음).
    옛 행을 만나면 KeyError로 파이프라인이 죽는 대신, 인덱스 없는 그룹은
    안전하게 "아무 단어에도 적용 안 함"(빈 리스트)으로 처리해야 한다 —
    엉뚱한 단어를 잘못 고쳐쓰는 것보다 안전한 방향이다."""
    from app.core.pipeline import _gender_groups_for_ai

    legacy_group = {"words": ["guapo"], "target_word_lemmas": ["guapo"], "gender": "male"}
    assert _gender_groups_for_ai([legacy_group]) == [{"candidate_indices": [], "gender": "male"}]


def test_build_resolved_registers_omits_gender_groups_until_all_referents_answered():
    """회귀(사용자 피드백 "인칭을 제대로 구분 못하는 경우가 있다"): 다인물
    줄은 인물(그룹) 전부가 답변될 때까지 gender_groups를 아예 만들지
    않는다 — 절반만 답한 상태로 적용을 시작하면 안 남은 인물의 단어가
    원문 그대로 남았다가 나중에 값이 생겨도 재적용되지 않을 위험이 있다."""
    from app.core.pipeline import _build_resolved_registers, registers_need_confirmation

    partially_answered = [{
        "segment_id": "pair_1", "gender_check_needed": True, "formality_check_needed": False,
        "resolved_gender": None, "resolved_formality": None,
        "resolved_gender_groups": [
            {"candidate_indices": [0], "gender": "female"},
            {"candidate_indices": [1], "gender": None},
        ],
    }]
    assert registers_need_confirmation(partially_answered) is True
    assert "pair_1" not in _build_resolved_registers(partially_answered)

    fully_answered = [{
        "segment_id": "pair_1", "gender_check_needed": True, "formality_check_needed": False,
        "resolved_gender": None, "resolved_formality": None,
        "resolved_gender_groups": [
            {"candidate_indices": [0], "gender": "female"},
            {"candidate_indices": [1], "gender": "male"},
        ],
    }]
    assert registers_need_confirmation(fully_answered) is False
    registers = _build_resolved_registers(fully_answered)
    assert registers["pair_1"]["gender_groups"] == [
        {"candidate_indices": [0], "gender": "female"},
        {"candidate_indices": [1], "gender": "male"},
    ]


def test_build_resolved_registers_keeps_gender_group_positions_around_not_applicable():
    """회귀: resolve_gender_groups_in_texts는 그룹을 lemma가 아니라 리스트
    위치(그룹 인덱스)로 매칭한다. 앞쪽 인물이 "해당없음"으로 답해도 그
    자리를 리스트에서 통째로 빼면 안 된다 — 빼면 뒷사람의 확정 성별이
    앞으로 밀려 엉뚱한 인물(원래 앞자리였던 사람)에게 적용된다. gender=None
    으로 자리만 지켜야 한다(resolve 쪽이 None gender는 이미 안전하게
    건너뛴다)."""
    from app.core.pipeline import _build_resolved_registers

    fully_answered = [{
        "segment_id": "pair_1", "gender_check_needed": True, "formality_check_needed": False,
        "resolved_gender": None, "resolved_formality": None,
        "resolved_gender_groups": [
            {"candidate_indices": [0], "gender": "not_applicable"},
            {"candidate_indices": [1], "gender": "male"},
        ],
    }]
    registers = _build_resolved_registers(fully_answered)
    assert registers["pair_1"]["gender_groups"] == [
        {"candidate_indices": [0], "gender": None},
        {"candidate_indices": [1], "gender": "male"},
    ]


@pytest.mark.asyncio
async def test_pipeline_applies_confirmed_gender_groups_to_correct_referent_before_dual_verification(
        tmp_path, monkeypatch):
    """엔드투엔드 회귀: 한 줄에 인물이 둘(성별 다름)이면 S1이 인물별로 따로
    감지하고, 검수자가 인물별로 답한 뒤(스테퍼를 흉내냄)에만 S2 이전에
    각자의 단어에만 정확히 반영돼야 한다 — 성별 하나를 문장 전체에 뭉뚱그려
    적용해 엉뚱한 인물까지 잘못 바뀌는 문제(사용자 피드백)의 회귀 테스트."""
    from app.core.pipeline import run_pipeline_phase1, run_pipeline_phase2, _build_resolved_registers
    from app.language_profiles.loader import load_profile
    from app.knowledge.loader import load_knowledge

    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nÉl está cansado y ella está enojado.\n",
        encoding="utf-8")
    provider = MockProvider()
    captured = {}

    async def _capture_correct_primary(pairs, *args, **kwargs):
        captured["pairs"] = pairs
        return []

    monkeypatch.setattr(provider, "correct_primary", _capture_correct_primary)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        phase1 = await run_pipeline_phase1(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            cached_korean_segments=[{"start": 0.0, "end": 2.0, "text": "그는 화나고 그녀는 피곤해"}],
            cached_video_proxy_path="/fake/proxy.mp4",
        )

    groups = phase1["segment_resolutions"][0]["resolved_gender_groups"]
    assert len(groups) == 2  # 회귀: 두 인물이 따로 감지돼야 함(하나로 뭉치면 안 됨)
    # 검수자가 스테퍼에서 인물별로 각각 답한 상태를 흉내낸다 — "cansado" 쪽
    # 인물은 여성, "enojado" 쪽 인물은 남성으로 확정.
    for group in groups:
        group["gender"] = "female" if "cansado" in group["words"] else "male"
    resolved_registers = _build_resolved_registers(phase1["segment_resolutions"])

    profile = load_profile("es", "LATAM")
    knowledge = load_knowledge()
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        await run_pipeline_phase2(
            phase1["pairs"], provider, profile, knowledge, phase1["pending_sensitive_hits"],
            "tv1", resolved_registers,
        )

    # "cansado"만 여성형(cansada)으로 바뀌고, "enojado"는 이미 확정된 남성과
    # 일치하므로 그대로다 — 각자의 인물에만 정확히 반영됐다는 뜻이다.
    assert captured["pairs"][0]["target_text"] == "[informal] Él está cansada y ella está enojado."


@pytest.mark.asyncio
async def test_dual_verification_reapplies_resolved_gender_to_llm_rewrite(tmp_path, monkeypatch):
    """회귀: S2가 오역 등 다른 문제를 고치며 문장을 통째로 다시 쓰면서, 이미
    확정된 성별(여성)을 무시하고 남성형으로 써버리는 사용자 리포트 —
    Claude/GPT가 합의해 자동 적용(approved)되는 경로라 더 위험하다.
    재검증(S2) 결과물에도 성별이 다시 강제 적용돼야 한다."""
    from app.core.pipeline import run_pipeline_phase1, run_pipeline_phase2
    from app.language_profiles.loader import load_profile
    from app.knowledge.loader import load_knowledge

    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nEstoy cansada.\n", encoding="utf-8")
    provider = MockProvider()

    async def _no_flags(pairs, *args, **kwargs):
        return []

    monkeypatch.setattr(provider, "correct_primary", _no_flags)
    monkeypatch.setattr(provider, "verify_and_refine", _no_flags)

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        phase1 = await run_pipeline_phase1(
            video_path="/fake/video.mp4", target_srt_path=str(srt_path),
            language="es", variant="LATAM", target_version_id="tv1", provider=provider,
            cached_korean_segments=[{"start": 0.0, "end": 2.0, "text": "나 피곤해"}],
            cached_video_proxy_path="/fake/proxy.mp4",
        )

    seg_id = phase1["pairs"][0].id
    # 검수자가 이미 "여성"으로 확정해둔 상태를 흉내낸다.
    resolved_registers = {seg_id: {"gender": "female", "formality": None}}

    async def _both_agree_on_masculine_rewrite(pairs, *args, **kwargs):
        return [{"segment_id": pairs[0]["id"], "category": "mistranslation",
                  "corrected_text": "Sí, ahora veo que estás muy cansado.",
                  "description": "번역 보정"}]

    monkeypatch.setattr(provider, "correct_primary", _both_agree_on_masculine_rewrite)
    monkeypatch.setattr(provider, "verify_and_refine", _both_agree_on_masculine_rewrite)

    profile = load_profile("es", "LATAM")
    knowledge = load_knowledge()
    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline_phase2(
            phase1["pairs"], provider, profile, knowledge, phase1["pending_sensitive_hits"],
            "tv1", resolved_registers,
        )

    finding = next(f for f in result["findings"] if f.model == "claude+gpt")
    assert finding.suggested_text == "Sí, ahora veo que estás muy cansada."
    assert finding.final_text == "Sí, ahora veo que estás muy cansada."
    final_pair = next(p for p in result["pairs"] if p.id == seg_id)
    assert final_pair.target.text == "Sí, ahora veo que estás muy cansada."


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


# 영화 전체 pair를 Claude/GPT 검증 콜 하나에 몰아넣으면 응답이 토큰 한도에서
# 잘려 파싱이 통째로 실패하거나(그 구간 전체 무효 처리), 모델이 segment_id를
# 엉뚱한 줄에 붙이는 오귀속이 늘어난다("원본"에 다른 타임코드의 문장이 붙는
# 버그로 실제 재현됨). 아래 테스트들은 그 회귀를 막는다: 씬 단위로 쪼개
# 호출하고, 씬 분할이 실패해도 타임코드 공백 기준으로 안전하게 폴백하는지
# 확인한다.
from app.core.ingest import build_srt
from app.core.pipeline import (
    CHUNK_GAP_SECONDS, CHUNK_MAX_SIZE, CHUNK_MIN_SIZE,
    _chunk_pairs_by_gap, _validate_scene_boundaries,
)
from app.schemas import AlignedPair, SegmentText


def _pair(id_: str, start: float, end: float) -> AlignedPair:
    return AlignedPair(
        id=id_, korean=SegmentText(start=start, end=end, text="k"),
        target=SegmentText(start=start, end=end, text="t"),
    )


def test_chunk_pairs_by_gap_keeps_one_chunk_when_below_min_size():
    pairs = [_pair(f"p{i}", i * 10.0, i * 10.0 + 1.0) for i in range(5)]
    assert _chunk_pairs_by_gap(pairs) == [pairs]


def test_chunk_pairs_by_gap_cuts_only_at_gap_after_min_size():
    pairs = []
    t = 0.0
    for i in range(CHUNK_MIN_SIZE):
        pairs.append(_pair(f"p{i}", t, t + 1.0))
        t += 1.1
    t += CHUNK_GAP_SECONDS
    for i in range(5):
        pairs.append(_pair(f"q{i}", t, t + 1.0))
        t += 1.1

    chunks = _chunk_pairs_by_gap(pairs)

    assert len(chunks) == 2
    assert len(chunks[0]) == CHUNK_MIN_SIZE
    assert len(chunks[1]) == 5


def test_chunk_pairs_by_gap_forces_cut_at_max_size_without_gap():
    pairs = []
    t = 0.0
    for i in range(CHUNK_MAX_SIZE + 5):
        pairs.append(_pair(f"p{i}", t, t + 1.0))
        t += 1.05  # 간격이 항상 CHUNK_GAP_SECONDS 미만이라 자연 절단점이 없음

    chunks = _chunk_pairs_by_gap(pairs)

    assert len(chunks[0]) == CHUNK_MAX_SIZE
    assert sum(len(c) for c in chunks) == len(pairs)


def test_validate_scene_boundaries_accepts_full_ordered_cover():
    pairs = [_pair(f"p{i}", float(i), i + 1.0) for i in range(5)]
    scenes = [{"start_id": "p0", "end_id": "p2"}, {"start_id": "p3", "end_id": "p4"}]

    chunks = _validate_scene_boundaries(scenes, pairs)

    assert [p.id for p in chunks[0]] == ["p0", "p1", "p2"]
    assert [p.id for p in chunks[1]] == ["p3", "p4"]


def test_validate_scene_boundaries_rejects_gap_between_scenes():
    pairs = [_pair(f"p{i}", float(i), i + 1.0) for i in range(5)]
    scenes = [{"start_id": "p0", "end_id": "p1"}, {"start_id": "p3", "end_id": "p4"}]  # p2 누락

    assert _validate_scene_boundaries(scenes, pairs) is None


def test_validate_scene_boundaries_rejects_unknown_id():
    pairs = [_pair(f"p{i}", float(i), i + 1.0) for i in range(2)]
    scenes = [{"start_id": "p0", "end_id": "nope"}]

    assert _validate_scene_boundaries(scenes, pairs) is None


@pytest.mark.asyncio
async def test_dual_verification_falls_back_to_gap_chunking_when_scene_split_fails(tmp_path, monkeypatch):
    """씬 분할 콜이 실패하면(재시도까지 실패) 영화 전체를 한 콜로 보내는 게
    아니라 타임코드 공백 기준으로 쪼갠 청크마다 correct_primary를 따로
    호출해야 한다."""
    n_first = CHUNK_MIN_SIZE + 3
    n_second = 4
    entries = []
    t = 0.0
    for i in range(n_first):
        entries.append({"start": t, "end": t + 1.0, "text": f"line {i}"})
        t += 1.1
    t += CHUNK_GAP_SECONDS
    for i in range(n_second):
        entries.append({"start": t, "end": t + 1.0, "text": f"line {n_first + i}"})
        t += 1.1

    srt_path = tmp_path / "target.srt"
    srt_path.write_text(build_srt(entries), encoding="utf-8")
    korean_segments = [{"start": e["start"], "end": e["end"], "text": f"한국어 {i}"}
                        for i, e in enumerate(entries)]

    provider = MockProvider()

    async def _fail_split(pairs, profile):
        raise ValueError("씬 분할 실패 시뮬레이션")
    monkeypatch.setattr(provider, "split_scenes", _fail_split)

    calls = []
    original_correct_primary = provider.correct_primary

    async def _capture(pairs, *args, **kwargs):
        calls.append(pairs)
        return await original_correct_primary(pairs, *args, **kwargs)
    monkeypatch.setattr(provider, "correct_primary", _capture)

    result = await run_pipeline(
        video_path="/fake/video.mp4", target_srt_path=str(srt_path),
        language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        cached_korean_segments=korean_segments, cached_video_proxy_path="/fake/proxy.mp4",
    )

    assert len(calls) == 2
    assert len(calls[0]) == n_first
    assert len(calls[1]) == n_second
    assert any(w["stage"] == "씬 분할" for w in result["warnings"])


@pytest.mark.asyncio
async def test_dual_verification_uses_valid_scene_boundaries_from_provider(tmp_path, monkeypatch):
    """씬 분할 콜이 유효한 경계를 돌려주면, 그 경계를 그대로 청크로 써서
    correct_primary를 씬별로 호출해야 한다."""
    entries = [{"start": i * 2.0, "end": i * 2.0 + 1.0, "text": f"line {i}"} for i in range(6)]
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(build_srt(entries), encoding="utf-8")
    korean_segments = [{"start": e["start"], "end": e["end"], "text": f"한국어 {i}"}
                        for i, e in enumerate(entries)]

    provider = MockProvider()

    async def _split(pairs, profile):
        return [{"start_id": "pair_1", "end_id": "pair_3", "summary": "a"},
                {"start_id": "pair_4", "end_id": "pair_6", "summary": "b"}]
    monkeypatch.setattr(provider, "split_scenes", _split)

    calls = []
    original_correct_primary = provider.correct_primary

    async def _capture(pairs, *args, **kwargs):
        calls.append([p["id"] for p in pairs])
        return await original_correct_primary(pairs, *args, **kwargs)
    monkeypatch.setattr(provider, "correct_primary", _capture)

    await run_pipeline(
        video_path="/fake/video.mp4", target_srt_path=str(srt_path),
        language="es", variant="LATAM", target_version_id="tv1", provider=provider,
        cached_korean_segments=korean_segments, cached_video_proxy_path="/fake/proxy.mp4",
    )

    assert calls == [["pair_1", "pair_2", "pair_3"], ["pair_4", "pair_5", "pair_6"]]


@pytest.mark.asyncio
async def test_run_grammar_necessity_check_auto_resolves_when_llm_gives_confident_gender(monkeypatch):
    """LLM이 확신 있는 성별을 돌려주면 그 그룹은 이미 확정된 채로 저장되어
    검수자에게 다시 묻지 않는다(design §확신 있으면 자동 확정)."""
    from app.core.pipeline import _run_grammar_necessity_check
    from app.schemas import SegmentText, AlignedPair
    from app.providers.mock import MockProvider

    class ConfidentProvider(MockProvider):
        async def resolve_gender_from_context(self, items, profile):
            return [
                {"id": item["id"], "words": [
                    {"index": 0, "is_person": True, "group_id": 0,
                     "gender": "male", "referent": "Juan"},
                ]}
                for item in items
            ]

    pairs = [AlignedPair(
        id="p1", korean=SegmentText(start=0.0, end=1.0, text="그 인간이 피곤해해."),
        target=SegmentText(start=0.0, end=1.0, text="Juan está cansado."),
    )]
    resolutions, warnings = await _run_grammar_necessity_check(
        pairs, {"language": "es", "variant": "LATAM"}, ConfidentProvider(), "tv1")
    assert warnings == []
    groups = resolutions[0]["resolved_gender_groups"]
    assert groups[0]["gender"] == "male"
    assert groups[0]["referent"] == "Juan"
    assert groups[0]["candidate_indices"] == [0]


@pytest.mark.asyncio
async def test_run_grammar_necessity_check_drops_line_when_llm_says_not_a_person(monkeypatch):
    """LLM이 후보 단어를 "사람 얘기 아님"으로 판단하면 그 줄은 성별 확인이
    필요 없다고 표시되어야 한다(design §오탐 제거, 예: tiempo compartido)."""
    from app.core.pipeline import _run_grammar_necessity_check
    from app.schemas import SegmentText, AlignedPair
    from app.providers.mock import MockProvider

    class NotApplicableProvider(MockProvider):
        async def resolve_gender_from_context(self, items, profile):
            return [
                {"id": item["id"], "words": [
                    {"index": 0, "is_person": False, "group_id": 0,
                     "gender": None, "referent": None},
                ]}
                for item in items
            ]

    pairs = [AlignedPair(
        id="p1", korean=SegmentText(start=0.0, end=1.0, text="그 인간이 타임셰어 멤버십을 줬어."),
        target=SegmentText(start=0.0, end=1.0, text="Me dio tiempo compartido."),
    )]
    resolutions, warnings = await _run_grammar_necessity_check(
        pairs, {"language": "es", "variant": "LATAM"}, NotApplicableProvider(), "tv1")
    assert warnings == []
    assert resolutions[0]["gender_check_needed"] is False
    assert resolutions[0]["resolved_gender_groups"] is None


@pytest.mark.asyncio
async def test_run_grammar_necessity_check_falls_back_to_unresolved_group_when_llm_fails(monkeypatch):
    """LLM 호출이 실패해도(과탐지 허용, 누락 금지 방침) 질문 자체는 사라지지
    않고 미확정 그룹으로 사람에게 넘어가야 한다."""
    from app.core.pipeline import _run_grammar_necessity_check
    from app.schemas import SegmentText, AlignedPair
    from app.providers.mock import MockProvider

    class FailingProvider(MockProvider):
        async def resolve_gender_from_context(self, items, profile):
            raise RuntimeError("network down")

    pairs = [AlignedPair(
        id="p1", korean=SegmentText(start=0.0, end=1.0, text="그 인간이 피곤해."),
        target=SegmentText(start=0.0, end=1.0, text="Estoy cansado."),
    )]
    resolutions, warnings = await _run_grammar_necessity_check(
        pairs, {"language": "es", "variant": "LATAM"}, FailingProvider(), "tv1")
    assert len(warnings) == 1
    assert resolutions[0]["gender_check_needed"] is True
    groups = resolutions[0]["resolved_gender_groups"]
    assert groups[0]["words"] == ["cansado"]
    assert groups[0]["gender"] is None


@pytest.mark.asyncio
async def test_pipeline_merges_sentence_split_across_target_cues_with_korean_srt(tmp_path):
    """핵심 회귀(사용자 재현): 한국어 SRT 한 큐의 번역이 스페인어 SRT
    큐 두 개로 나뉘어 있으면, 지금은(큐 기반 정렬) 하나의 Segment로
    합쳐져야 한다 — 예전(단어 단위)엔 한국어 문장이 중간에서 잘렸다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nEs todo tu culpa.\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n¿Lo sabías?\n",
        encoding="utf-8",
    )
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n이게 다 너 때문이야\n", encoding="utf-8",
    )

    class KoreanMockProvider(MockProvider):
        async def transcribe(self, audio_path):
            return [
                {"start": 0.0, "end": 0.5, "text": "이게"},
                {"start": 0.5, "end": 1.0, "text": "다"},
                {"start": 1.0, "end": 1.5, "text": "너"},
                {"start": 1.5, "end": 2.0, "text": "때문이야"},
            ]

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=KoreanMockProvider(),
            korean_srt_path=str(ko_srt_path),
        )

    korean_pairs = [p for p in result["pairs"] if p.korean is not None]
    assert len(korean_pairs) == 1
    # MockProvider가 격식 자동판정 결과를 "[informal] " 접두어로 표시한다
    # (한국어 "때문이야"가 반말 어미라 자동 확정됨) — 큐 기반 정렬로 두 스페인어
    # 큐가 하나로 합쳐졌는지가 이 테스트의 핵심이라, 접두어는 그대로 반영한다.
    assert korean_pairs[0].target.text == "[informal] Es todo tu culpa. ¿Lo sabías?"


@pytest.mark.asyncio
async def test_pipeline_falls_back_to_word_align_when_cached_segments_lack_cue_index(tmp_path):
    """회귀: korean_srt_path가 주어졌어도, 캐시된 korean_segments가
    (구버전 캐시 등으로) cue_index를 안 갖고 있으면 크래시 대신 기존
    단어 단위 align()으로 안전하게 폴백해야 한다."""
    srt_path = tmp_path / "target.srt"
    srt_path.write_text(TARGET_SRT, encoding="utf-8")
    ko_srt_path = tmp_path / "ko.srt"
    ko_srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n안녕하세요\n", encoding="utf-8",
    )

    with patch("app.core.pipeline.extract_audio") as mock_extract, \
         patch("app.core.pipeline.generate_video_proxy"):
        result = await run_pipeline_phase1(
            video_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1", provider=MockProvider(),
            cached_korean_segments=[{"start": 0.0, "end": 2.0, "text": "안녕하세요"}],
            cached_video_proxy_path="/fake/cached_proxy.mp4",
            korean_srt_path=str(ko_srt_path),
        )

    # 영상 동기화 오프셋 탐지용으로 짧게는 호출된다(design §2026-08 영상
    # 동기화 버그 수정) — 이 테스트가 검증하려는 cue_index 폴백과는 무관.
    mock_extract.assert_called_once()
    korean_pair = next(p for p in result["pairs"] if p.korean is not None)
    assert korean_pair.korean.text == "안녕하세요"


@pytest.mark.asyncio
async def test_run_dual_verification_pass_skips_pairs_without_korean_text():
    """한국어 원문이 없는 pair(스페인어만 있는 반쪽짜리)는 S2 AI 검증
    자체를 건너뛰어야 한다 — 비교할 원문이 없는 상태에서 오역 여부를
    판단하면 근거 없는 오탐만 만든다(design §스페인어만 있는 경우)."""
    from app.core.pipeline import _run_dual_verification_pass
    pairs = [
        AlignedPair(id="p1", korean=SegmentText(start=0.0, end=1.0, text="안녕"),
                    target=SegmentText(start=0.0, end=1.0, text="BAD_TRANSLATION uno")),
        AlignedPair(id="p2", korean=None,
                    target=SegmentText(start=1.0, end=2.0, text="BAD_TRANSLATION dos")),
    ]
    findings, warnings = await _run_dual_verification_pass(
        pairs, MockProvider(), {"language": "es", "variant": "LATAM"},
        [], "", "", "tv1", {},
    )
    segment_ids = {f.segment_id for f in findings}
    assert "p1" in segment_ids
    assert "p2" not in segment_ids


@pytest.mark.asyncio
async def test_run_dual_verification_pass_warns_when_pairs_skipped_for_missing_korean():
    """한국어 원문이 없어 S2를 건너뛴 pair가 있으면, 왜 그런지 검수자에게
    보이는 경고가 남아야 한다 — 조용히 사라지면 안 된다."""
    from app.core.pipeline import _run_dual_verification_pass
    pairs = [
        AlignedPair(id="p1", korean=SegmentText(start=0.0, end=1.0, text="안녕"),
                    target=SegmentText(start=0.0, end=1.0, text="Hola")),
        AlignedPair(id="p2", korean=None,
                    target=SegmentText(start=1.0, end=2.0, text="Texto sin coreano")),
    ]
    findings, warnings = await _run_dual_verification_pass(
        pairs, MockProvider(), {"language": "es", "variant": "LATAM"},
        [], "", "", "tv1", {},
    )
    assert any("건너뛴 줄 1건" in w["message"] for w in warnings)
