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
