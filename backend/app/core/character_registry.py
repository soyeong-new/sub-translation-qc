from typing import List
from app.schemas import AlignedPair
from app.providers.base import ModelProvider


async def build_registry(pairs: List[AlignedPair], profile: dict,
                          provider: ModelProvider) -> dict:
    """checks_enabled의 gender_agreement/register_consistency 중 하나라도
    켜져 있을 때만 LLM 호출로 인물 식별을 수행한다 (design §5, §5-1 비용 절감
    원칙과 동일한 이유 — 성/수 구분도 격식 체계도 없는 언어라면 이 단계 전체를
    생략한다)."""
    checks = profile.get("checks_enabled", {})
    if not (checks.get("gender_agreement") or checks.get("register_consistency")):
        return {"characters": [], "relationships": []}
    pair_dicts = [
        {"id": p.id, "target_text": p.target.text if p.target else ""}
        for p in pairs
    ]
    return await provider.analyze_characters(pair_dicts, profile)
