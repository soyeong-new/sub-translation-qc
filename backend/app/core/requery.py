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
from app.core.pipeline import (
    _normalize_gender_for_ai, gender_groups_all_resolved, _build_gender_groups_from_llm,
)
from app.repositories import get_pending_findings_for_segment

_FORMAT_CONSTRAINT = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."


class RequeryNotSupportedError(ValueError):
    pass


async def requery_finding(finding: FindingRow, segment: Segment, instruction: str,
                           provider: ModelProvider, knowledge: str, profile: dict) -> str:
    """finding을 만든 모델에게 단일 재검증을 맡긴다(claude는 correct_primary,
    gpt/claude+gpt는 verify_and_refine) — 검수자가 이미 지시사항으로 방향을
    정한 상태라, 원래의 이중 독립검증(합의 필요)만큼 신중할 필요가 없다.
    순수 규칙 기반(사전필터, 그리고 안전망 중 rewrap_line만으로 해결된
    "자동재배치")은 재질문 대상이 아니다 — 판단을 내린 LLM이 없으므로
    검수자가 직접 "수정"을 쓰는 게 맞다."""
    current_text = finding.final_text or finding.suggested_text or segment.target_text
    extra_instruction = instruction
    item = [{"id": segment.id, "korean_text": segment.korean_text, "target_text": current_text}]

    if finding.model == "claude":
        results = await provider.correct_primary(
            item, profile, [], knowledge, _FORMAT_CONSTRAINT, extra_instruction=extra_instruction,
        )
    elif finding.model in ("gpt", "claude+gpt"):
        results = await provider.verify_and_refine(
            item, profile, [], knowledge, _FORMAT_CONSTRAINT, extra_instruction=extra_instruction,
        )
    elif finding.model == "안전망":
        return await provider.shrink_line(
            current_text, MAX_LINE_CHARS, MAX_LINES, extra_instruction=extra_instruction)
    else:
        raise RequeryNotSupportedError(
            "규칙 기반 finding은 다시 질문하기 대상이 아닙니다. 직접 수정을 이용하세요.")

    if not results:
        return current_text
    return results[0]["corrected_text"]


async def reverify_segment_after_stt_correction(
    segment: Segment, provider: ModelProvider, knowledge: str, profile: dict,
    current_text: Optional[str] = None,
) -> Optional[dict]:
    """STT 원문이 수정된 직후, 그 줄의 번역이 새 원문 기준으로도 여전히
    맞는지 GPT 하나로만 가볍게 재검증한다 — "다시 질문하기"와 같은 원칙으로
    단일 모델만 쓰고 이중 독립검증(합의 필요)은 하지 않는다. 글자수 제약은
    다른 모든 검증 경로와 동일하게(줄당 MAX_LINE_CHARS자, 최대 MAX_LINES줄)
    유지한다. 문제 없으면(GPT가 아무 교정도 안 돌려주면) None.

    current_text가 있으면(이 세그먼트에 finding이 이미 정확히 하나 있는
    경우) segment.target_text(원본 그대로) 대신 그 finding의 제안문을
    기준으로 재검증한다 — 안 그러면 "이미 다른 이유로 수정 제안이 나와
    있는 줄"을 GPT가 항상 원본(target_text)만 보고 판단해서, 원본은
    그럭저럭 괜찮다는 이유로 기존 제안이 새 원문 기준으로도 맞는지는
    한 번도 확인 안 하고 넘어가는 문제(회귀: 사용자 재현 — STT 고쳐도
    기존 제안 카드가 그대로였음)가 생긴다."""
    text_to_check = current_text if current_text is not None else segment.target_text
    results = await provider.verify_and_refine(
        [{"id": segment.id, "korean_text": segment.korean_text, "target_text": text_to_check}],
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
              "groups": [{"candidate_indices": g.get("candidate_indices") or [], "gender": g["gender"]} for g in groups]}],
            language)
        return fixed[segment.id]
    gender = _normalize_gender_for_ai(segment.resolved_gender_raw)
    if gender:
        fixed = resolve_gender_in_texts([{"id": segment.id, "text": text, "gender": gender}], language)
        return fixed[segment.id]
    return text


async def flag_new_gender_ambiguity(
    segment: Segment, flags: dict, provider: ModelProvider, profile: dict,
) -> bool:
    """check_grammar_necessity의 gender_check_needed는 "이 줄에 성별 표시
    문법이 있다"는 구조적 판단일 뿐, "아직 답 안 됐다"는 뜻이 아니다.
    (1) 단일값(resolved_gender_raw)으로 이미 답변된 줄이고 재검출된 후보가
    여전히 하나뿐이면(구조가 안 바뀌었으면) 이미 커버된 것 — 새로 물을
    필요 없다.
    (2) 재검출된 후보 단어(lemma 기준)의 전체 집합이 기존 그룹들의 lemma
    전체 집합과 정확히 같으면(단어가 하나도 안 바뀌었으면) 역시 이미
    커버된 것으로 본다.
    (3) 그 외(후보가 새로 생겼거나 구성이 바뀌었으면)에는 pipeline.py의
    _run_grammar_necessity_check와 같은 방식으로 LLM에 그룹핑+성별
    판단을 다시 맡기고, 그 결과로 기존 그룹을 통째로 교체한다 — 기존
    답과 새 후보를 부분적으로 짜맞추는 건 엉뚱한 답을 엉뚱한 단어에
    붙일 위험이 있다(design §그룹핑도 LLM이 직접)."""
    if not flags.get("gender_check_needed"):
        return False
    candidate_words = flags.get("candidate_words") or []
    candidate_lemmas = flags.get("candidate_word_lemmas") or []
    existing = segment.resolved_gender_groups_raw or []

    if (not existing and len(candidate_words) <= 1
            and _normalize_gender_for_ai(segment.resolved_gender_raw)):
        return False

    existing_lemmas = sorted(lemma for g in existing for lemma in g["target_word_lemmas"])
    if existing and existing_lemmas == sorted(candidate_lemmas):
        return False

    llm_item = {
        "id": segment.id, "target_text": segment.target_text,
        "korean_text": segment.korean_text, "candidate_words": candidate_words,
    }
    try:
        llm_results = await provider.resolve_gender_from_context(
            [{"id": llm_item["id"], "target_text": llm_item["target_text"],
              "korean_text": llm_item["korean_text"], "candidate_words": candidate_words}],
            profile)
        groups_by_id = _build_gender_groups_from_llm(
            [{**llm_item, "candidate_word_lemmas": candidate_lemmas}], llm_results)
    except Exception:
        # LLM 호출 자체의 실패든(네트워크/타임아웃), 스키마는 지켰지만
        # 그룹핑이 불가능한 응답(예: group_id가 해시 불가능한 타입)이든
        # 같은 방식으로 처리한다 — 이 줄은 새 그룹 없이 그대로 두고 사람이
        # "다시 질문하기"로 재요청할 수 있게 한다(500으로 STT 교정
        # 엔드포인트 전체를 죽이면 안 된다).
        groups_by_id = {}
    new_groups = groups_by_id.get(segment.id)
    if not new_groups:
        return False

    segment.resolved_gender_groups_raw = new_groups
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
