"""한국어-대상언어 텍스트를 대조해 오역/번역투/로컬라이제이션 문제를 찾는 모듈."""

from typing import List
from pydantic import ValidationError
from app.schemas import AlignedPair, Finding
from app.providers.base import ModelProvider
from app.core.format_rules import MAX_LINE_CHARS, MAX_LINES

FORMAT_CONSTRAINT = f"줄당 {MAX_LINE_CHARS}자 이내, 세그먼트당 최대 {MAX_LINES}줄을 지켜서 제안할 것."


async def run_translation_review(pairs: List[AlignedPair], profile: dict,
                                  knowledge: str, provider: ModelProvider,
                                  target_version_id: str) -> List[Finding]:
    """오역/번역투/로컬라이제이션을 하나의 LLM 호출로 함께 탐지한다. 포맷팅
    제약(design §5-1의 2번 지점)을 프롬프트에 포함해 제안 문장이 애초에 줄
    길이 규칙을 지키도록 유도하지만, 이것만으로 완전히 보장되지는 않으므로
    export 직전 안전망 체크가 여전히 필요하다."""
    pair_dicts = [
        {"id": p.id, "korean_text": p.korean.text if p.korean else "",
         "target_text": p.target.text if p.target else ""}
        for p in pairs if p.target is not None
    ]
    raw_findings = await provider.review_translation(
        pair_dicts, knowledge, profile, FORMAT_CONSTRAINT)
    pair_by_id = {p.id: p for p in pairs}
    findings = []
    # 같은 모델이 같은 segment_id+category에 대해 finding을 두 개 이상 돌려줄
    # 수 있다 (예: 한 줄에서 오역과 로컬라이제이션 뉘앙스를 각각 지적). id가
    # finding_{segment_id}_{category}{model}로만 결정되면 두 번째 항목이 첫
    # 번째와 동일한 PK를 갖게 되어 저장 시 IntegrityError로 job 전체가 실패한다
    # (이미 API 비용은 다 쓴 뒤). (segment_id, category, model) 조합별 등장
    # 횟수를 세어 두 번째부터는 _2, _3... 접미사를 덧붙인다 — 흔한 경우(조합당
    # finding 1개)는 기존 id 형식이 그대로 유지된다.
    id_occurrence_counts: dict = {}
    for rf in raw_findings:
        try:
            pair = pair_by_id[rf["segment_id"]]
            model = rf.get("model")
            id_suffix = f"_{model}" if model else ""
            dedup_key = (rf["segment_id"], rf["category"], model)
            id_occurrence_counts[dedup_key] = id_occurrence_counts.get(dedup_key, 0) + 1
            ordinal = id_occurrence_counts[dedup_key]
            ordinal_suffix = f"_{ordinal}" if ordinal > 1 else ""
            findings.append(Finding(
                id=f"finding_{rf['segment_id']}_{rf['category']}{id_suffix}{ordinal_suffix}",
                target_version_id=target_version_id,
                segment_id=rf["segment_id"],
                category=rf["category"],
                description=rf["description"],
                original_text=pair.target.text if pair.target else "",
                suggested_text=rf["suggested_text"],
                confidence=rf["confidence"],
                source="llm",
                model=model,
            ))
        except (KeyError, ValidationError):
            # Skip malformed items (unknown segment_id, missing required fields,
            # or invalid field values that fail Finding validation)
            continue
    return findings
