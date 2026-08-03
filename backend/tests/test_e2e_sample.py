import pytest
from pathlib import Path
from unittest.mock import patch
from app.core.pipeline import run_pipeline
from app.providers.mock import MockProvider

SAMPLE_SRT = (
    Path(__file__).parent.parent.parent
    / "ES 자막 표본_1" / "AI 번역(XL8)" / "ThePeachTree_es.srt"
)


@pytest.mark.asyncio
async def test_pipeline_runs_end_to_end_on_real_sample_file(monkeypatch):
    monkeypatch.setenv("QC_PROVIDER", "mock")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert SAMPLE_SRT.exists(), "샘플 SRT 파일이 존재해야 함"

    with patch("app.core.pipeline.extract_audio", return_value="/fake/audio.wav"), \
         patch("app.core.pipeline.generate_video_proxy", return_value="/fake/proxy.mp4"), \
         patch("app.core.pipeline.delete_original_video", return_value=None):
        result = await run_pipeline(
            video_path="/fake/video.mp4",
            target_srt_path=str(SAMPLE_SRT),
            language="es", variant="LATAM",
            target_version_id="tv_e2e",
            provider=MockProvider(),
        )

    assert len(result["pairs"]) > 0
    assert isinstance(result["findings"], list)
    assert isinstance(result["format_violations"], list)
