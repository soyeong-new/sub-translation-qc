"""LLM 호출 전에 사전/정규식으로 처리 가능한 항목(#3/#4/#6)을 먼저 처리하는 모듈."""

import re
from dataclasses import dataclass, field
from typing import List, Tuple
from app.schemas import AlignedPair, Finding


@dataclass
class PretreatmentResult:
    pairs: List[AlignedPair]
    findings: List[Finding] = field(default_factory=list)
    pending_sensitive_hits: List[dict] = field(default_factory=list)


def _apply_glossary(text: str, entries: List[dict]) -> Tuple[str, List[str]]:
    applied = []
    for entry in entries:
        canonical = entry["canonical"]
        for alias in entry.get("aliases", []):
            if alias != canonical and alias in text:
                text = text.replace(alias, canonical)
                applied.append(f"{alias} → {canonical}")
    return text, applied


def _apply_cta_patterns(text: str, patterns: List[str]) -> Tuple[str, List[str]]:
    applied = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            applied.append(pattern)
    return text, applied


def _apply_profanity_dictionary(text: str, entries: List[dict]) -> Tuple[str, List[str]]:
    applied = []
    for entry in entries:
        term = entry["term"]
        if term in text:
            text = text.replace(term, entry["replacement"])
            applied.append(term)
    return text, applied


def _make_finding(target_version_id: str, segment_id: str, category: str,
                   description: str, original_text: str, suggested_text: str) -> Finding:
    return Finding(
        id=f"finding_{segment_id}_pretreatment_{category}",
        target_version_id=target_version_id, segment_id=segment_id,
        category=category, description=description,
        original_text=original_text, suggested_text=suggested_text,
        confidence=1.0, source="rule", model="사전필터",
        status="approved", final_text=suggested_text,
    )


def find_pending_sensitive_hits(pairs: List[AlignedPair], sensitive_terms: List[str],
                                 profanity_entries: List[dict]) -> List[dict]:
    """target_text가 이미 사전처리(글로서리/CTA/비속어사전 치환)를 거친 뒤에도
    남아있는 민감어 후보만 찾는다. run_pretreatment 안에서 쓰이는 것과 별개로,
    성별/격식 확인이 끝난 뒤 검증(S2)을 재개할 때 — 사전처리는 이미 끝난
    pairs에 대해 민감어 매칭만 다시 계산해야 할 때 재사용한다."""
    applied_terms = {e["term"] for e in profanity_entries}
    hits: List[dict] = []
    for pair in pairs:
        if pair.target is None:
            continue
        text_lower = pair.target.text.lower()
        for term in sensitive_terms:
            if term.lower() in text_lower and term not in applied_terms:
                hits.append({"segment_id": pair.id, "term": term})
    return hits


def run_pretreatment(pairs: List[AlignedPair], glossary_entries: List[dict],
                      cta_patterns: List[str], profanity_entries: List[dict],
                      sensitive_terms: List[str], target_version_id: str) -> PretreatmentResult:
    """design §전체 파이프라인 S1: #3(뻔한 비속어)·#4(글로서리)·#6(CTA)을 LLM 없이
    먼저 처리한다. profanity_entries에 없는 민감어 후보(sensitive_terms 매칭)는
    애매한 경우로 보고 Claude 1차로 넘긴다(pending_sensitive_hits)."""
    findings: List[Finding] = []

    for pair in pairs:
        if pair.target is None:
            continue
        original = pair.target.text
        text = original

        text, glossary_hits = _apply_glossary(text, glossary_entries)
        text, cta_hits = _apply_cta_patterns(text, cta_patterns)
        text, profanity_hits = _apply_profanity_dictionary(text, profanity_entries)

        if text != original:
            pair.target.text = text
            if glossary_hits:
                findings.append(_make_finding(
                    target_version_id, pair.id, "glossary",
                    f"고유명사 표기 통일: {', '.join(glossary_hits)}", original, text))
            if cta_hits:
                findings.append(_make_finding(
                    target_version_id, pair.id, "cta",
                    "구독/좋아요 등 홍보 문구 삭제", original, text))
            if profanity_hits:
                findings.append(_make_finding(
                    target_version_id, pair.id, "sensitivity",
                    f"사전 등록된 비속어 자동 교정: {', '.join(profanity_hits)}", original, text))

    pending_sensitive_hits = find_pending_sensitive_hits(pairs, sensitive_terms, profanity_entries)

    return PretreatmentResult(pairs=pairs, findings=findings,
                               pending_sensitive_hits=pending_sensitive_hits)
