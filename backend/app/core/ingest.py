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
         "-c:v", "libx264", "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-y", out],
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


_LEADING_DASH_RE = re.compile(r"^-\s*")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_PAREN_PREFIX_RE = re.compile(r"^\([^)]*\)\s*")


def _clean_korean_cue_lines(text: str) -> List[str]:
    """사용자 제공 한국어 SRT에는 효과음/노래(`[...]`, `♪...`)와 화자
    표기(`(이름) 대사`)가 섞여 있다 — STT는 만들어내지 않는 노이즈라 그대로
    두면 대사가 아닌 텍스트가 한국어 원문에 섞인다. 화자 이름은 추출해
    쓰지 않고 버린다(design §범위 밖 — 과거 인물 로스터 제거 이력과 같은
    이유로, 화자를 안다고 그 줄의 문법적 성별 지시 대상까지 아는 건
    아니라서).

    실제 SRT에서는 한 큐 안에 화자가 둘 이상일 때 줄 앞에 "- "를 붙이고
    (예: "- (경리) 아닌데요\n- (순모) 어?"), 효과음/지문 대괄호가 대사와
    같은 줄에 붙기도 한다(예: "- (순모) [떨리는 목소리로] 현아"). 대괄호는
    줄 전체가 아니라 줄 어디에 있든 제거한다 — 화자 괄호만 줄 맨 앞
    접두어로 취급한다(문장 중간의 괄호를 화자 표기와 구분할 근거가
    없어서, 이건 앞머리에서만 나타나는 관례를 따른다).

    "- " 표시가 있으면(화자 둘 이상이 같은 큐 시간대에 겹쳐 말함) 줄을
    하나로 이어붙이지 않고 발화별로 따로 반환한다 — 이어붙이면 호출자가
    큐의 [start,end] 구간을 글자 수 비례로 순서대로 나눠 갖는데, 실제로는
    "순서대로 말한 것"이 아니라 "같은 시간대에 겹쳐/따로 말한 것"이라
    한쪽에 억지로 이른 시각을, 다른 쪽에 늦은 시각을 떠넘기게 돼 실제로
    대응하는 대상언어 큐와 어긋나는 사고가 있었다(design
    2026-08-11-korean-srt-input-design.md 후속 논의) — 발화별로 따로
    돌려주면 호출자가 각 발화에 큐 전체 구간을 그대로 주고, 어느 대상언어
    큐와 겹치는지는 align()의 겹침 판정에 맡길 수 있다. "- " 표시가 없으면
    (한 사람의 대사가 화면 폭 때문에 여러 줄로 나뉜 경우) 지금까지처럼
    하나로 합친다."""
    raw_lines = text.split("\n")
    is_multi_speaker = any(line.strip().startswith("-") for line in raw_lines)

    kept_lines = []
    for line in raw_lines:
        line = _LEADING_DASH_RE.sub("", line.strip()).strip()
        if not line or line.startswith("♪"):
            continue
        line = _BRACKET_RE.sub("", line).strip()
        line = _PAREN_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        kept_lines.append(line)

    if not kept_lines:
        return []
    if is_multi_speaker:
        return kept_lines
    return [" ".join(kept_lines)]


def _words_with_interpolated_timecodes(text: str, start: float, end: float) -> List[dict]:
    """SRT 큐는 문장 전체의 시작/끝만 알려주고 단어별 실제 발화 시각은
    모른다 — 글자 수가 발화 시간과 대략 비례한다고 보고(균등 분할보다
    나은 근사, design §2), 큐의 [start,end] 구간을 단어별 글자 수 비율로
    나눠 STT 단어 타임코드를 흉내 낸다."""
    words = text.split()
    if not words:
        return []
    total_chars = sum(len(w) for w in words)
    duration = end - start
    result = []
    cursor = start
    for word in words:
        word_end = cursor + duration * (len(word) / total_chars)
        result.append({"start": cursor, "end": word_end, "text": word})
        cursor = word_end
    return result


def korean_words_from_srt(path: str) -> List[dict]:
    """사용자가 이미 갖고 있는 한국어 SRT를 STT 없이 한국어 대사 소스로
    쓴다(design 2026-08-11-korean-srt-input-design.md). align()이 문장이
    아니라 단어 단위 타임코드를 기대하므로(§2), 정제 후 단어로 쪼개
    글자 수 비례로 타임코드를 보간한다. 반환 모양이 STT transcribe()와
    동일해 이후 align()/detect_global_offset()을 무수정으로 재사용한다."""
    words: List[dict] = []
    for cue in load_srt(path):
        for line_text in _clean_korean_cue_lines(cue.text):
            words.extend(_words_with_interpolated_timecodes(line_text, cue.start, cue.end))
    return words
