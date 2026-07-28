import re
import subprocess
from pathlib import Path
from typing import List, Optional
from app.schemas import SegmentText

_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


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


def extract_audio(video_path: str, out_dir: Optional[str] = None) -> str:
    """원본 영상에서 오디오 트랙을 16kHz mono WAV로 뽑아낸다. v1은 음원분리 없이
    이 결과를 그대로 STT에 넣는다 (design §3, §11-3)."""
    out_dir_p = Path(out_dir) if out_dir else Path(video_path).parent
    out_dir_p.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    out = str(out_dir_p / f"{stem}_16k.wav")
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", "-y", out],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    return out
