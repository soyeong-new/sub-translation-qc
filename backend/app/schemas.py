"""API/파이프라인 전반에서 쓰이는 핵심 Pydantic 데이터 모델 정의."""

from typing import Literal, Optional
from pydantic import BaseModel, Field

FindingCategory = Literal[
    "gender", "register", "translation", "localization", "sensitivity", "formatting"
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


class ExportStats(BaseModel):
    finding_count: int
    reflection_rate: float = Field(..., ge=0.0, le=1.0)
