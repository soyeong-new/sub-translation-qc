"""검수자가 '다시 질문하기'로 재요청한 finding 하나, 또는 STT 교정 직후 그
세그먼트 하나만 재분석하는 모듈."""

import asyncio
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import FindingRow, Segment
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES
from app.core.grammar_necessity import (
    check_grammar_necessity, resolve_gender_in_texts, resolve_gender_groups_in_texts,
)
from app.core.pipeline import _normalize_gender_for_ai, gender_groups_all_resolved
from app.repositories import get_pending_findings_for_segment

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


def apply_resolved_gender_to_text(segment: Segment, text: str, language: str) -> str:
    """1차 검수 때 이미 확정된 성별을 나중에 생긴 새 텍스트(STT 재검증
    제안문구 등)에도 반영한다. pipeline._apply_resolved_gender와 같은
    우선순위(그룹이 전부 답변됐으면 그룹, 아니면 단일값)를 따르지만
    pairs 리스트가 아니라 문자열 하나를 받는다 — 파이프라인이 끝난
    뒤에도(리뷰 화면에서) 재사용하기 위해서다."""
    groups = segment.resolved_gender_groups_raw
    if groups and gender_groups_all_resolved(groups):
        fixed = resolve_gender_groups_in_texts(
            [{"id": segment.id, "text": text,
              "groups": [{"lemmas": g["target_word_lemmas"], "gender": g["gender"]} for g in groups]}],
            language)
        return fixed[segment.id]
    gender = _normalize_gender_for_ai(segment.resolved_gender_raw)
    if gender:
        fixed = resolve_gender_in_texts([{"id": segment.id, "text": text, "gender": gender}], language)
        return fixed[segment.id]
    return text


def flag_new_gender_ambiguity(segment: Segment, flags: dict) -> bool:
    """check_grammar_necessity의 gender_check_needed는 "이 줄에 성별 표시
    문법이 있다"는 구조적 판단일 뿐, "아직 답 안 됐다"는 뜻이 아니다 —
    이미 apply_resolved_gender_to_text로 반영된 줄도 그 형용사 자체는
    여전히 성별 표시 형용사라 다시 감지된다. 그래서 여기서는 감지된 인물
    그룹이 기존 확정값으로 이미 커버되는지 직접 판단한다: (1) 단일값
    (resolved_gender_raw)으로 이미 답변된 줄이고 재검출된 인물이 여전히
    하나뿐이면(구조가 안 바뀌었으면) 이미 커버된 것 — 새로 물을 필요 없다.
    (2) 그 외에는 감지된 각 인물 그룹의 lemma 조합이 기존
    resolved_gender_groups_raw에 이미 있는지로 판단한다. 커버 안 되는
    그룹만 미답변으로 추가하고 True를 반환한다."""
    if not flags.get("gender_check_needed"):
        return False
    detected_groups = flags.get("gender_groups") or []
    existing = segment.resolved_gender_groups_raw or []

    if (not existing and len(detected_groups) <= 1
            and _normalize_gender_for_ai(segment.resolved_gender_raw)):
        return False

    existing_lemma_sets = {tuple(sorted(g["target_word_lemmas"])) for g in existing}
    new_groups = [
        {"words": g["words"], "target_word_lemmas": g["lemmas"], "gender": None,
         "referent": g.get("referent")}
        for g in detected_groups
        if tuple(sorted(g["lemmas"])) not in existing_lemma_sets
    ]
    if not new_groups:
        return False
    combined = existing + new_groups
    # grammatical_person(1/2/3인칭)은 문장 전체를 기준으로 계산돼서 "이 그룹이
    # 정확히 누구 얘기인지"까지는 구분 못 한다 — 인물이 이 줄에 하나뿐일
    # 때만(기존 pipeline._run_grammar_necessity_check와 동일한 조건) 그
    # 하나의 그룹에 붙여도 안전하다. 둘 이상이면 어느 그룹 것인지 알 길이
    # 없으니 아예 안 붙인다.
    if len(combined) == 1:
        combined[0] = {**combined[0], "person": flags.get("grammatical_person")}
    segment.resolved_gender_groups_raw = combined
    segment.gender_check_needed = True
    return True


async def gloss_new_gender_words(segment: Segment, provider: ModelProvider, profile: dict) -> None:
    """flag_new_gender_ambiguity가 새로 추가한 그룹은 뜻풀이(word_meanings)가
    없다 — 메인 파이프라인의 _gloss_gender_words(pipeline.py)와 같은 이유로
    (대상언어를 모르는 검수자가 "이 단어가 사람 얘기인지조차" 판단 못 하는
    문제) STT 재검증 경로에서 새로 생긴 그룹에도 똑같이 뜻풀이를 채운다.
    이미 word_meanings가 있는 그룹(재검증을 여러 번 거친 경우)은 다시
    안 부른다."""
    groups = segment.resolved_gender_groups_raw or []
    items: list = []
    entries: list = []
    for group_index, group in enumerate(groups):
        if group.get("word_meanings"):
            continue
        for w in group.get("words") or []:
            items.append({"id": str(len(entries)), "word": w, "context": segment.target_text})
            entries.append((group_index, w))
    if not items:
        return
    results = await provider.gloss_words(items, profile)
    meaning_by_idx = {r["id"]: r.get("meaning") for r in results}
    new_groups = [dict(g) for g in groups]
    for idx, (group_index, word) in enumerate(entries):
        meaning = meaning_by_idx.get(str(idx))
        if not meaning:
            continue
        word_meanings = dict(new_groups[group_index].get("word_meanings") or {})
        word_meanings[word] = meaning
        new_groups[group_index]["word_meanings"] = word_meanings
    segment.resolved_gender_groups_raw = new_groups


async def reapply_gender_to_pending_findings(session: AsyncSession, segment: Segment, language: str) -> None:
    """사람이 성별을 (다시) 답했을 때, 그 세그먼트에 이미 만들어진 pending
    finding들의 제안문구에도 새 답을 반영한다 — STT 재검증이 성별 확인이
    필요한 제안문구를 만들어놓고 사람 답을 기다리던 경우를 위해서다."""
    pending = await get_pending_findings_for_segment(session, segment.id)
    for finding in pending:
        finding.suggested_text = await asyncio.to_thread(
            apply_resolved_gender_to_text, segment, finding.suggested_text, language)
