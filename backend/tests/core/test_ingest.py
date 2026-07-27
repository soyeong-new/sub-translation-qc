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
