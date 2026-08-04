from app.core.ingest import parse_srt, build_srt

SAMPLE = """1
00:00:01,000 --> 00:00:02,500
Hola mundo

2
00:00:03,000 --> 00:00:04,000
¿Cómo estás?
"""


def test_parse_srt_extracts_segments_with_seconds():
    segments = parse_srt(SAMPLE)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].end == 2.5
    assert segments[0].text == "Hola mundo"


def test_parse_srt_skips_empty_blocks():
    segments = parse_srt(SAMPLE + "\n\n")
    assert len(segments) == 2


def test_build_srt_round_trips_timestamps():
    out = build_srt([{"start": 1.0, "end": 2.5, "text": "Hola mundo"}])
    assert "00:00:01,000 --> 00:00:02,500" in out
    assert "Hola mundo" in out


TWO_LINE_SAMPLE = """1
00:00:01,000 --> 00:00:02,500
Primera línea
Segunda línea
"""


def test_parse_srt_preserves_line_breaks_within_a_cue():
    """줄바꿈을 공백으로 합치면 "최대 2줄" 규칙이 발동할 수 없고 줄당 글자수
    규칙도 이어붙인 한 줄에 적용돼 오탐이 된다 (design §5-1)."""
    segments = parse_srt(TWO_LINE_SAMPLE)
    assert len(segments) == 1
    assert segments[0].text == "Primera línea\nSegunda línea"
    assert " ".join(["Primera línea", "Segunda línea"]) != segments[0].text


def test_parse_srt_build_srt_round_trips_two_line_cue():
    segments = parse_srt(TWO_LINE_SAMPLE)
    out = build_srt([{"start": s.start, "end": s.end, "text": s.text} for s in segments])
    assert "Primera línea\nSegunda línea" in out
    assert parse_srt(out)[0].text == segments[0].text


from unittest.mock import patch
from app.core.ingest import generate_video_proxy, delete_original_video


def test_generate_video_proxy_calls_ffmpeg_with_scale_filter(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"fake")
    with patch("subprocess.run") as mock_run:
        out = generate_video_proxy(str(video), out_dir=str(tmp_path / "proxy"))
    assert out == str(tmp_path / "proxy" / "input_proxy.mp4")
    args = mock_run.call_args.args[0]
    assert "ffmpeg" in args
    assert "-vf" in args
    assert "scale=-2:480" in args


def test_delete_original_video_removes_file(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"fake")
    delete_original_video(str(video))
    assert not video.exists()


def test_delete_original_video_does_not_raise_when_already_missing(tmp_path):
    delete_original_video(str(tmp_path / "does-not-exist.mp4"))
