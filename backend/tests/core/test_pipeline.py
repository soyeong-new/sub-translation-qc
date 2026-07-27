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

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"):
        result = await run_pipeline(
            korean_audio_path="/fake/video.mp4",
            target_srt_path=str(srt_path),
            language="es", variant="LATAM",
            target_version_id="tv1",
            provider=MockProvider(),
        )

    assert any(f.category == "translation" for f in result["findings"])
    assert any(v.rule == "ellipsis" for v in result["format_violations"])
    assert "pairs" in result
