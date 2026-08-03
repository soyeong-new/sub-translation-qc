"""API/파이프라인 전반에서 쓰이는 핵심 Pydantic 데이터 모델 정의."""

from typing import Literal, Optional
from pydantic import BaseModel, Field

FindingCategory = Literal[
    "gender", "register", "translation", "localization", "sensitivity", "formatting", "glossary", "cta"
]
FindingStatus = Literal["pending", "approved", "rejected", "modified"]


class SegmentText(BaseModel):
    start: float
    end: float
    text: str


class AlignedPair(BaseModel):
    id: str
    korean: Optional[SegmentText] = None
    target: Optional[SegmentText] = None
    alignment_confidence: float = 1.0


class Character(BaseModel):
    id: str
    title_id: str
    label: str
    confirmed_gender: Optional[Literal["male", "female"]] = None


class Relationship(BaseModel):
    id: str
    title_id: str
    speaker_character_id: str
    addressee_character_id: str
    confirmed_formality_level: Optional[Literal["formal", "informal"]] = None


class Finding(BaseModel):
    id: str
    target_version_id: str
    segment_id: str
    category: FindingCategory
    description: str = Field(..., description="반드시 한국어")
    original_text: str
    suggested_text: str
    confidence: float
    source: Literal["rule", "llm"] = "llm"
    model: Optional[str] = None
    status: FindingStatus = "pending"
    final_text: str = ""
    reviewer_name: str = ""
    reviewed_at: Optional[str] = None


class FormatViolation(BaseModel):
    segment_id: str
    rule: Literal["line_length", "ellipsis"]
    detail: str
    auto_fixed: bool = False
    fixed_text: str = ""
    # 위반이 감지된 그 시점의 텍스트(체크포인트 스냅샷)다. 온점 위반은 파이프라인
    # 안에서 여러 시점(최초 체크, GPT 이후 최종 재체크)에 검사되므로, 이 값이
    # 없으면 나중에(예: repositories.py) 파이프라인이 이미 끝난 뒤의 최종 텍스트로
    # original_text를 잘못 재구성하게 된다 — 같은 세그먼트가 두 체크포인트 모두에서
    # 걸렸을 때 두 finding의 "고친 전" 텍스트가 실제로는 서로 다른데도 똑같이
    # (그리고 틀리게) 표시되는 버그로 이어진다. 비워두면(레거시 호출자) 호출자가
    # 직접 채워야 한다.
    original_text: str = ""


class ExportStats(BaseModel):
    finding_count: int
    reflection_rate: float = Field(..., ge=0.0, le=1.0)
