"""검수자가 '다시 질문하기'로 재요청한 finding 하나, 또는 STT 교정 직후 그
세그먼트 하나만 재분석하는 모듈."""

from typing import Optional
from app.models import FindingRow, Segment
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES

_LLM_REQUERYABLE_MODELS = ("claude", "gpt", "claude+gpt")
_FORMAT_CONSTRAINT = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."


class RequeryNotSupportedError(ValueError):
    pass


async def requery_finding(finding: FindingRow, segment: Segment, instruction: str,
                           provider: ModelProvider, knowledge: str, profile: dict) -> str:
    """finding을 만든 모델이 무엇이었든(claude/gpt/claude+gpt) GPT 하나로만
    단일 재검증한다 — 검수자가 이미 지시사항으로 방향을 정한 상태라, 원래의
    이중 독립검증(합의 필요)만큼 신중할 필요가 없다. 순수 규칙 기반(사전필터,
    그리고 안전망 중 rewrap_line만으로 해결된 "자동재배치")은 재질문 대상이
    아니다 — 판단을 내린 LLM이 없으므로 검수자가 직접 "수정"을 쓰는 게 맞다."""
    current_text = finding.final_text or finding.suggested_text or segment.target_text

    if finding.model in _LLM_REQUERYABLE_MODELS:
        results = await provider.verify_and_refine(
            [{"id": segment.id, "korean_text": segment.korean_text, "target_text": current_text}],
            profile, [], knowledge, _FORMAT_CONSTRAINT, extra_instruction=instruction,
        )
    elif finding.model == "안전망":
        return await provider.shrink_line(
            current_text, MAX_LINE_CHARS, MAX_LINES, extra_instruction=instruction)
    else:
        raise RequeryNotSupportedError(
            "규칙 기반 finding은 다시 질문하기 대상이 아닙니다. 직접 수정을 이용하세요.")

    if not results:
        return current_text
    return results[0]["corrected_text"]


async def reverify_segment_after_stt_correction(
    segment: Segment, provider: ModelProvider, knowledge: str, profile: dict,
) -> Optional[dict]:
    """STT 원문이 수정된 직후, 그 줄의 번역이 새 원문 기준으로도 여전히
    맞는지 GPT 하나로만 가볍게 재검증한다 — "다시 질문하기"와 같은 원칙으로
    단일 모델만 쓰고 이중 독립검증(합의 필요)은 하지 않는다. 글자수 제약은
    다른 모든 검증 경로와 동일하게(줄당 MAX_LINE_CHARS자, 최대 MAX_LINES줄)
    유지한다. 문제 없으면(GPT가 아무 교정도 안 돌려주면) None."""
    results = await provider.verify_and_refine(
        [{"id": segment.id, "korean_text": segment.korean_text, "target_text": segment.target_text}],
        profile, [], knowledge, _FORMAT_CONSTRAINT,
    )
    return results[0] if results else None
