"""API/파이프라인 전반에서 쓰이는 핵심 Pydantic 데이터 모델 정의."""

from typing import Literal, Optional
from pydantic import BaseModel, Field

FindingCategory = Literal[
    "mistranslation", "nuance_tone", "unnatural_style", "locale_convention",
    "sensitivity", "formatting", "glossary", "cta",
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
    gender: Optional[str] = None
    formality: Optional[str] = None


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
    rule: Literal["line_length", "ellipsis", "reading_speed", "glossary_mismatch"]
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
    # glossary_mismatch 전용 — original_text 안에서 canonical 대신 실제로
    # 쓰인 표기(예: 오타, 잘못된 대체어). 프론트에서 이 부분만 하이라이트
    # 표시하는 데 쓴다. 못 찾았거나 다른 rule이면 빈 문자열.
    matched_text: str = ""
    # matched_text의 간결한 한국어 뜻 — 검수자가 대상언어를 몰라도 뭐가
    # 바뀐 건지 알 수 있게 한다. matched_text가 없으면 이것도 빈 문자열.
    matched_meaning: str = ""
    # matched_text조차 없을 때(흔적 없이 사라진 경우)만 채워지는, 최종
    # 텍스트 전체의 한국어 요약 — 대상언어를 모르는 검수자도 그 줄에 실제로
    # 뭐라고 쓰여 있는지 알 수 있게 한다. matched_text가 있으면 빈 문자열.
    text_gloss: str = ""


class ExportStats(BaseModel):
    finding_count: int
    reflection_rate: float = Field(..., ge=0.0, le=1.0)
