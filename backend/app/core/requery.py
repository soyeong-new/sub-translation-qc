"""검수자가 '다시 질문하기'로 재요청한 finding 하나만 재분석하는 모듈."""

from typing import Optional

from app.models import FindingRow, Segment
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES


class RequeryNotSupportedError(ValueError):
    pass


async def requery_finding(finding: FindingRow, segment: Segment, instruction: str,
                           provider: ModelProvider, knowledge: str, profile: dict,
                           resolved_character: Optional[dict] = None,
                           resolved_relationship: Optional[dict] = None) -> str:
    """finding.model(생성 단계)에 맞는 프로바이더 메서드를 그 세그먼트 하나만
    대상으로 다시 호출하고, 새 suggested_text를 반환한다. 규칙 기반(사전필터/
    null)은 재질문 대상이 아니다 — LLM에게 물어볼 대상이 없으므로 검수자가
    직접 "수정"을 쓰는 게 맞다.

    resolved_character/resolved_relationship: 이 세그먼트가 이미 인물/관계에
    연결되어 있으면(Segment.resolved_character_id 등) 그 인물/관계 정보를
    넘겨준다 — 없으면(즉답값으로 해결됐거나 아직 미해결) None. Claude
    재질문 시 이 정보가 없으면 성별/격식 맥락 없이 다시 교정하게 되어 원래
    교정과 다른 결과가 나올 수 있다."""
    format_constraint = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."
    current_text = finding.final_text or finding.suggested_text or segment.target_text

    if finding.model == "claude":
        characters = [resolved_character] if resolved_character else []
        relationships = [resolved_relationship] if resolved_relationship else []
        results = await provider.correct_primary(
            [{"id": segment.id, "korean_text": segment.korean_text, "target_text": current_text}],
            profile, characters, relationships, [], knowledge, format_constraint,
            extra_instruction=instruction,
        )
    elif finding.model == "gpt":
        results = await provider.verify_and_refine(
            [{"id": segment.id, "korean_text": segment.korean_text, "current_text": current_text}],
            {}, profile, knowledge, format_constraint, extra_instruction=instruction,
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
