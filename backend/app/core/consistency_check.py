"""확정되지 않은 인물 성별/관계 격식 중 검수자 확인이 필요한 항목을 찾는 모듈."""

from typing import List


def find_gender_conflicts(characters: List[dict]) -> List[dict]:
    """title_id에 confirmed_gender가 이미 저장돼 있으면(과거 화/다른 언어
    버전에서 확인 완료) 재확인하지 않는다 — design §6의 title 단위 공유 원칙."""
    return [
        c for c in characters
        if c.get("confirmed_gender") is None and c.get("gendered_segment_ids")
    ]


def find_register_conflicts(relationships: List[dict]) -> List[dict]:
    return [
        r for r in relationships
        if r.get("confirmed_formality_level") is None and r.get("formality_segment_ids")
    ]
