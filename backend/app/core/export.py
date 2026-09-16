"""검수 결과를 반영해 최종 SRT를 조립하고 반영율 통계를 계산하는 모듈."""

import logging
import re
from typing import List, Optional
from app.core.ingest import build_srt
from app.core.format_rules import check_line_length
from app.providers.base import ModelProvider
from app.schemas import AlignedPair, SegmentText, ExportStats, FormatViolation

logger = logging.getLogger(__name__)

_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def build_export_filename(title_name: str, episode_no: Optional[int],
                           target_language: str, variant: str) -> str:
    """내보내기 SRT 파일명을 만든다 — 타이틀명_(회차)_언어코드_변형.srt.
    회차(episode_no)가 있으면 "N화"를 언어 앞에 끼워 넣는다."""
    safe_title = _FILENAME_UNSAFE_RE.sub("", title_name).strip()
    parts = [safe_title]
    if episode_no is not None:
        parts.append(f"{episode_no}화")
    parts.append(f"{target_language}_{variant}")
    return "_".join(parts) + ".srt"


def _final_text_by_segment(findings: List[dict]) -> dict:
    """세그먼트별 최종 텍스트 맵.

    한 세그먼트에 여러 finding이 걸릴 수 있다 (예: 자동보정된 온점 위반과
    검수자가 직접 고친 줄 길이 위반이 같은 세그먼트를 가리키는 경우). 지켜야 할
    불변식은 "검수자의 명시적 판단은 자동 적용된 기계적 보정을 항상 이긴다"이다.

    그 신호로 reviewed_at을 쓴다: review-action 엔드포인트만 이 값을 채우고,
    save_pipeline_result가 만드는 자동보정 finding은 NULL로 남는다. source는
    finding을 '누가 만들었는지'일 뿐 '누가 해결했는지'가 아니라서 이 판단에
    쓸 수 없다 — 검수자가 규칙 기반 finding을 modified로 고쳐도 source는
    "rule"로 남기 때문이다.

    검수되지 않은 것 먼저, 검수된 것 나중에 적용해 뒤에 오는 쪽이 이기게 한다.
    이 정렬이 없으면 결과가 DB의 행 반환 순서에 좌우된다."""
    reflected = [
        f for f in findings
        if f["status"] in ("approved", "modified") and f["final_text"]
    ]
    reflected.sort(key=lambda f: f.get("reviewed_at") is not None)
    return {f["segment_id"]: f["final_text"] for f in reflected}


def assemble_final_srt(segments: List[dict], findings: List[dict]) -> str:
    """최종 SRT를 조립한다.

    세 가지를 보정한다:
    1. 대상언어 텍스트가 빈 세그먼트(정렬되지 않은 한국어 전용 세그먼트)는
       건너뛴다 — 빈 큐는 정보 가치가 없고 대부분의 SRT 플레이어가 깨진 자막으로
       취급한다.
    2. 검수자가 "제외"로 표시한 세그먼트(excluded=True)는 건너뛴다(design
       2026-08-13-korean-srt-cue-based-segmentation-design.md §신규: 제외
       표시) — 겹치는 한국어 원문을 못 찾은 반쪽짜리 번역 줄을 검수자가
       직접 뺄 수 있게 한다.
    3. 저장 순서(index)가 아니라 실제 타임코드(start) 순으로 정렬한다 —
       alignment 정렬 함수가 짝을 못 찾은 대상언어 세그먼트를 뒤에 몰아 붙이기
       때문에 index 순서는 시간 순서와 일치하지 않는다.
    """
    final_by_segment = _final_text_by_segment(findings)
    entries = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        if seg.get("excluded"):
            continue
        text = final_by_segment.get(seg["id"], seg["text"])
        if not text.strip():
            continue
        entries.append({"start": seg["start"], "end": seg["end"], "text": text})
    return build_srt(entries)


def safety_net_check(segments: List[dict], findings: List[dict]) -> list:
    """export 직전 안전망 (design §5-1의 3번 지점). 검수자의 직접 수정 텍스트
    까지 포함한 최종 텍스트를 대상으로 줄 길이 규칙(50자, 최대 2줄)을 마지막으로
    한 번 더 검사한다. 제외된(excluded) 세그먼트는 최종 SRT에 안 나가므로
    검사할 이유가 없다."""
    final_by_segment = _final_text_by_segment(findings)
    pairs = [
        AlignedPair(id=seg["id"], target=SegmentText(
            start=seg["start"], end=seg["end"],
            text=final_by_segment.get(seg["id"], seg["text"]),
        ))
        for seg in segments if not seg.get("excluded")
    ]
    return check_line_length(pairs)


def _find_glossary_candidates(segments: List[dict], findings: List[dict],
                               glossary_entries: List[dict]) -> list:
    """1차 필터 — 문자열 매칭으로 값싸게 후보만 추린다. 대명사로 자연스럽게
    대체되거나 생략된 정당한 경우까지 전부 걸리므로(과탐), 이 후보만 LLM
    2차 판정(check_glossary_reflection)에 넘겨 진짜 위반만 남긴다."""
    final_by_segment = _final_text_by_segment(findings)
    candidates = []
    for seg in segments:
        if seg.get("excluded"):
            continue
        korean_text = seg.get("korean_text", "")
        text = final_by_segment.get(seg["id"], seg["text"])
        if not korean_text or not text:
            continue
        for entry in glossary_entries:
            canonical, korean_term = entry.get("canonical"), entry.get("korean_term")
            if not canonical or not korean_term:
                continue
            if korean_term in korean_text and canonical not in text:
                candidates.append({
                    "id": f"{seg['id']}:{entry.get('entry_id')}",
                    "segment_id": seg["id"], "korean_term": korean_term,
                    "canonical": canonical, "korean_text": korean_text, "text": text,
                })
    return candidates


async def glossary_consistency_check(segments: List[dict], findings: List[dict],
                                      glossary_entries: List[dict],
                                      provider: ModelProvider, profile: dict) -> list:
    """export 직전 안전망 — 등록된 고유명사 표준 표기가 최종 텍스트에 실제로
    남아 있는지 마지막으로 한 번 더 확인한다. 검수 시점 자동 보정
    (findings.py review-action/pick)이 닿지 못한 경우(finding 자체가 없는
    세그먼트, 거부돼 원본이 그대로 남은 세그먼트)를 잡기 위한 것 — 자동
    수정은 하지 않고 참고용 경고만 만든다(non-blocking).

    2단계 판정: 문자열 매칭 후보 중 "대명사로 정당하게 대체된 경우"를 LLM이
    걸러내고 진짜 오타/누락만 남긴다(design 논의 — 문자열 매칭만으로는
    "다르다"만 알 뿐 "왜 다른지"는 모름). LLM 호출이 실패하거나 응답에서
    id가 빠지면 위반으로 간주한다 — 과탐지 허용, 누락 금지."""
    candidates = _find_glossary_candidates(segments, findings, glossary_entries)
    if not candidates:
        return []
    try:
        results = await provider.check_glossary_reflection(candidates, profile)
        violation_by_id = {r["id"]: r["violation"] for r in results}
    except Exception:
        logger.exception("용어집 반영 확인(LLM) 실패, 후보 전부 경고로 처리")
        violation_by_id = {}
    return [
        FormatViolation(
            segment_id=c["segment_id"], rule="glossary_mismatch",
            detail=f"'{c['korean_term']}' 등록 표기 '{c['canonical']}'가 최종 텍스트에 없음",
            original_text=c["text"],
        )
        for c in candidates
        if violation_by_id.get(c["id"], True)
    ]


def compute_stats(findings: List[dict]) -> ExportStats:
    total = len(findings)
    reflected = sum(1 for f in findings if f["status"] in ("approved", "modified"))
    return ExportStats(
        finding_count=total,
        reflection_rate=(reflected / total) if total else 0.0,
    )
