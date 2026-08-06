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


import wave
from app.core.ingest import split_audio_into_chunks


def _write_silent_wav(path, seconds, sample_rate=16000):
    """16kHz mono 16bit PCM 무음 WAV를 지정한 길이(초)만큼 실제로 만든다.
    split_audio_into_chunks가 wave 모듈로 길이를 정확히 읽어야 하므로,
    가짜 바이트가 아니라 진짜 WAV 헤더/프레임이 필요하다."""
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)


def test_split_audio_into_chunks_returns_original_path_when_short_enough(tmp_path):
    wav = tmp_path / "short.wav"
    _write_silent_wav(wav, seconds=3.0)
    with patch("subprocess.run") as mock_run:
        result = split_audio_into_chunks(str(wav), chunk_seconds=10.0)
    assert result == [str(wav)]
    mock_run.assert_not_called()


def test_split_audio_into_chunks_returns_original_path_when_file_missing(tmp_path):
    missing = tmp_path / "does-not-exist.wav"
    with patch("subprocess.run") as mock_run:
        result = split_audio_into_chunks(str(missing), chunk_seconds=10.0)
    assert result == [str(missing)]
    mock_run.assert_not_called()


def test_split_audio_into_chunks_returns_original_path_when_file_is_not_valid_wav(tmp_path):
    garbage = tmp_path / "garbage.wav"
    garbage.write_bytes(b"this is not a wav file")
    with patch("subprocess.run") as mock_run:
        result = split_audio_into_chunks(str(garbage), chunk_seconds=10.0)
    assert result == [str(garbage)]
    mock_run.assert_not_called()


import subprocess
import pytest


def test_split_audio_into_chunks_cleans_up_partial_files_when_ffmpeg_fails(tmp_path):
    """회귀(Finding #2): ffmpeg의 segment muxer는 조각 파일을 처리하는 대로
    바로 디스크에 쓴다. 디스크 부족/손상된 입력 등으로 일부 조각을 쓴 뒤
    subprocess.run이 CalledProcessError를 던지면, 이미 쓰인 조각 파일이
    고아로 남으면 안 된다 — 실제 ffmpeg 동작을 흉내내어 mock이 파일 두
    개를 실제로 써 놓은 뒤 실패하도록 하고, 실패 후 그 파일들이 사라졌는지
    확인한다."""
    wav = tmp_path / "long.wav"
    _write_silent_wav(wav, seconds=5.0)
    chunks_dir = tmp_path / "chunks"

    def _fake_run(args, **kwargs):
        # 진짜 ffmpeg처럼 실패 직전까지 일부 조각 파일을 실제로 써 놓는다.
        chunks_dir.mkdir(parents=True, exist_ok=True)
        (chunks_dir / "long_chunk000.wav").write_bytes(b"partial chunk 0")
        (chunks_dir / "long_chunk001.wav").write_bytes(b"partial chunk 1")
        raise subprocess.CalledProcessError(returncode=1, cmd=args)

    with patch("subprocess.run", side_effect=_fake_run):
        with pytest.raises(subprocess.CalledProcessError):
            split_audio_into_chunks(str(wav), chunk_seconds=2.0, out_dir=str(chunks_dir))

    assert not (chunks_dir / "long_chunk000.wav").exists()
    assert not (chunks_dir / "long_chunk001.wav").exists()


def test_split_audio_into_chunks_splits_long_audio_and_calls_ffmpeg_segment_muxer(tmp_path):
    wav = tmp_path / "long.wav"
    _write_silent_wav(wav, seconds=5.0)
    with patch("subprocess.run") as mock_run:
        result = split_audio_into_chunks(str(wav), chunk_seconds=2.0, out_dir=str(tmp_path / "chunks"))
    # ceil(5.0 / 2.0) == 3개 조각
    assert result == [
        str(tmp_path / "chunks" / "long_chunk000.wav"),
        str(tmp_path / "chunks" / "long_chunk001.wav"),
        str(tmp_path / "chunks" / "long_chunk002.wav"),
    ]
    args = mock_run.call_args.args[0]
    assert "ffmpeg" in args
    assert "-f" in args and args[args.index("-f") + 1] == "segment"
    assert "-segment_time" in args and args[args.index("-segment_time") + 1] == "2.0"
    assert "-c" in args and args[args.index("-c") + 1] == "copy"
    assert "-reset_timestamps" in args and args[args.index("-reset_timestamps") + 1] == "1"
    assert str(tmp_path / "chunks" / "long_chunk%03d.wav") in args
