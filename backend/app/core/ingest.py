"""SRT 파일을 파싱/조립하고 영상에서 오디오를 추출하는 모듈."""

import re
import subprocess
import wave
from math import ceil
from pathlib import Path
from typing import List, Optional
from app.schemas import SegmentText
from app.core.uploads import MEDIA_ROOT

_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
# <i>, <b> 등 스타일 태그 — 자막 표시용 장식일 뿐 의미가 없는데, 태그 문자가
# 그대로 남으면 글자수 기반 규칙(format_rules 줄길이/읽기속도)이 실제 화면
# 글자수보다 부풀려 세거나, 임베딩/형태소 분석에 잡음이 섞인다. 파싱 시점에
# 한 번만 제거하면 이후 모든 단계가 이미 깨끗한 텍스트를 받는다.
_STYLE_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(content: str) -> List[SegmentText]:
    segments = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        time_idx = next((i for i, ln in enumerate(lines) if _TIME_RE.search(ln)), None)
        if time_idx is None:
            continue
        m = _TIME_RE.search(lines[time_idx])
        # 줄바꿈은 자막의 의미 있는 구조다 — 공백으로 합치면 "세그먼트당 최대
        # 2줄" 규칙이 아예 발동할 수 없고, 줄당 50자 규칙도 원래의 각 줄이 아니라
        # 이어붙인 한 줄에 적용돼 대량의 오탐이 생긴다. 또 build_srt가 이 텍스트를
        # 그대로 다시 쓰므로 export 결과의 줄바꿈이 원본과 달라진다.
        text = "\n".join(lines[time_idx + 1:]).strip()
        text = _STYLE_TAG_RE.sub("", text).strip()
        if not text:
            continue
        segments.append(SegmentText(
            start=_to_seconds(*m.groups()[0:4]),
            end=_to_seconds(*m.groups()[4:8]),
            text=text,
        ))
    return segments


def load_srt(path: str) -> List[SegmentText]:
    with open(path, encoding="utf-8-sig") as f:
        return parse_srt(f.read())


def _format_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(entries: List[dict]) -> str:
    blocks = []
    for i, e in enumerate(entries, start=1):
        blocks.append(
            f"{i}\n{_format_timestamp(e['start'])} --> {_format_timestamp(e['end'])}\n{e['text']}\n"
        )
    return "\n".join(blocks)


def extract_audio(video_path: str, out_dir: Optional[str] = None,
                   duration_seconds: Optional[float] = None) -> str:
    """원본 영상에서 오디오 트랙을 16kHz mono WAV로 뽑아낸다. v1은 음원분리 없이
    이 결과를 그대로 STT에 넣는다 (design §3, §11-3). duration_seconds를 주면
    처음 그 길이만큼만 잘라 추출한다 — 영상 앞부분 몇 분만 필요한 용도(예:
    영상 동기화 오프셋 탐지)에서 전체 오디오를 뽑는 비용을 아낀다."""
    out_dir_p = Path(out_dir) if out_dir else Path(video_path).parent
    out_dir_p.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    out = str(out_dir_p / f"{stem}_16k.wav")
    # -t를 -i 앞(입력 옵션)에 둔다 — 뒤(출력 옵션)에 두면 ffmpeg이 입력
    # 전체를 계속 디코딩하면서 출력만 잘라내 duration_seconds를 준 목적
    # (앞부분만 빠르게 뽑기)이 무색해진다.
    cmd = ["ffmpeg"]
    if duration_seconds is not None:
        cmd += ["-t", str(duration_seconds)]
    cmd += ["-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-y", out]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return out


def generate_video_proxy(video_path: str, out_dir: Optional[str] = None) -> str:
    """검수 화면 재생용 저화질(480p) 프록시를 만든다. 오디오 추출과 인물 판단엔
    원본 화질이 필요 없고, 검수 시 장면 맥락·성별 확인 같은 시각적 확인엔
    저화질로 충분하다 — 영상은 검수 끝까지 필요하지만 원본 그대로는 아니다."""
    out_dir_p = Path(out_dir) if out_dir else MEDIA_ROOT / "video_proxy"
    out_dir_p.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    out = str(out_dir_p / f"{stem}_proxy.mp4")
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-vf", "scale=-2:480",
         "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
         "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-y", out],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    return out


def delete_original_video(video_path: str) -> None:
    """프록시 생성 후 원본을 지운다. 스토리지 한도 안에서 여러 작품을 처리하려면
    필수 동작이다. 파일이 이미 없어도(중복 호출 등) 에러 없이 넘어간다."""
    Path(video_path).unlink(missing_ok=True)


def split_audio_into_chunks(wav_path: str, chunk_seconds: float = 600.0,
                             out_dir: Optional[str] = None) -> List[str]:
    """STT API의 파일 크기/길이 제한 안에 들어오도록 긴 오디오를 여러 조각으로 나눈다.
    16kHz mono 16bit PCM WAV는 초당 32KB라, 25MB는 약 781초(≈13분)에 해당한다
    — chunk_seconds 기본값(600초=10분)은 이보다 여유 있게 낮췄다.

    길이를 읽을 수 없거나(파일이 없거나 유효한 WAV가 아니거나) 이미
    chunk_seconds보다 짧으면 원본 경로를 그대로 담은 리스트를 반환한다 —
    이 폴백 덕분에 호출자가 별도 분기 없이 항상 "경로 리스트를 순서대로
    돌면서 처리"하는 동일한 코드로 두 경우(분할함/안 함)를 모두 처리할 수
    있다."""
    try:
        with wave.open(wav_path, "rb") as w:
            duration = w.getnframes() / w.getframerate()
    except (FileNotFoundError, wave.Error):
        return [wav_path]
    if duration <= chunk_seconds:
        return [wav_path]

    out_dir_p = Path(out_dir) if out_dir else Path(wav_path).parent
    out_dir_p.mkdir(parents=True, exist_ok=True)
    stem = Path(wav_path).stem
    num_chunks = ceil(duration / chunk_seconds)
    pattern = str(out_dir_p / f"{stem}_chunk%03d.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-i", wav_path, "-f", "segment", "-segment_time", str(chunk_seconds),
             "-c", "copy", "-reset_timestamps", "1", "-y", pattern],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    except subprocess.CalledProcessError:
        # ffmpeg의 segment muxer는 조각 파일을 처리하는 대로 즉시 디스크에
        # 쓴다 — 디스크 공간 부족, 손상된 입력, 강제 종료 등으로 일부 조각을
        # 쓴 뒤 실패하면 그 조각들이 아무도 정리하지 않는 고아 파일로
        # 남는다. 여기서 실패 시점까지 만들어졌을 조각 파일을 결정론적
        # 이름 패턴으로 찾아 지우고 나서 예외를 그대로 다시 던진다.
        for partial in out_dir_p.glob(f"{stem}_chunk*.wav"):
            partial.unlink(missing_ok=True)
        raise
    return [str(out_dir_p / f"{stem}_chunk{i:03d}.wav") for i in range(num_chunks)]
