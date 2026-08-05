"""Postgres에 매핑되는 SQLAlchemy ORM 테이블 정의 모듈."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, Boolean, ForeignKey, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class Title(Base):
    __tablename__ = "titles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # movie | series
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    chart_image_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # none: 업로드 안 함 | processing: vision 추출 중 | review_needed: 추출 완료, 확인 대기
    # confirmed: 사람이 검토 완료 | failed: 추출 실패
    chart_extraction_status: Mapped[str] = mapped_column(String, default="none")
    chart_extraction_error: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    episode_no: Mapped[int | None] = mapped_column(nullable=True)
    video_path: Mapped[str] = mapped_column(String)
    # STT/영상 프록시 캐시 — 같은 에피소드의 여러 target_version이 재사용하고,
    # 재시도 시에도 원본 영상 없이(최초 성공 후 삭제됨) 재분석이 가능해진다.
    stt_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    video_proxy_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 영어 SRT(선택) — 성별 확인이 필요한 줄에 대명사 힌트를 붙이는 참고
    # 자료로만 쓰인다(design §영어 SRT 대조: 자동 확정에는 쓰지 않음).
    # Episode 레벨인 이유는 한국어 영상/대상언어 SRT와 동일 — 같은 화를 여러
    # target_version(언어)으로 분석해도 참고할 영어 대사는 하나로 공유된다.
    english_srt_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    label: Mapped[str] = mapped_column(String)
    confirmed_gender: Mapped[str | None] = mapped_column(String, nullable=True)
    # vision 추출이 제안한 성별(참고용, 확정 아님) — confirmed_gender가 이미 있으면
    # 덮어쓰지 않는다(save_chart_extraction_result 참고).
    suggested_gender: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 이 인물 레코드가 어디서 만들어졌는지: "chart_image" | "manual" | None(기존 파이프라인 생성분)
    source: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class Relationship(Base):
    __tablename__ = "relationships"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    speaker_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"))
    addressee_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"))
    confirmed_formality_level: Mapped[str | None] = mapped_column(String, nullable=True)
    # 관계 유형(예: "연인", "남매", "직장 상사") — 인물관계도 이미지에서 추출되거나
    # 사람이 직접 입력. confirmed_formality_level(존댓말/반말)과는 별개 개념이다.
    relationship_type: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class TargetVersion(Base):
    __tablename__ = "target_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    target_language: Mapped[str] = mapped_column(String)
    variant: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="analyzing")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    video_proxy_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 파이프라인 단계별 부분 실패(예: 인물 식별 실패)를 사람에게 보여주기 위한
    # 목록. [{"stage": "인물 식별", "message": "..."}] 형태. 전부 성공하면 None.
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    index: Mapped[int] = mapped_column()
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    korean_text: Mapped[str] = mapped_column(String, default="")
    target_text: Mapped[str] = mapped_column(String, default="")
    # 문법 필요성 판단(줄 단위 LLM) 결과 — 이 값이 True인 세그먼트만 앵커 매칭·
    # 사람 리뷰 대상이 된다.
    gender_check_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    formality_check_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 해결 결과 — 인물 연결(앵커 있음, 재사용 가능) 또는 즉답값(앵커 없음, 이
    # 세그먼트에만 적용) 둘 중 하나만 채워진다. 성별과 격식은 서로 독립이라
    # 한 세그먼트에 둘 다 걸리면 네 필드 중 최대 2개(성별 계열 1개 + 격식 계열
    # 1개)가 채워질 수 있다.
    resolved_character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True, default=None)
    resolved_gender_raw: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    resolved_relationship_id: Mapped[str | None] = mapped_column(
        ForeignKey("relationships.id"), nullable=True, default=None)
    resolved_formality_raw: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 앵커 매칭 결과(분석 시점) — 이 세그먼트가 속한 씬에서 이름이 실제로 언급된
    # 로스터 인물 후보 목록. [{"id": str, "label": str}, ...] 형태를 그대로
    # 저장해 읽을 때 추가 조회 없이 바로 검수 UI의 "후보에서 고르기" 버튼을
    # 채울 수 있게 한다. 후보가 없거나 애초에 체크가 필요 없었던 세그먼트는 None.
    gender_anchor_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    formality_anchor_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # 영어 SRT 대명사 힌트(분석 시점 계산, 참고용) — {"text", "he_count",
    # "she_count"} 형태. 영어 SRT가 없거나 겹치는 대사가 없으면 None.
    # gender_check_needed인 세그먼트에만 계산된다(design §영어 SRT 대조:
    # "걸린 줄과 시간대가 겹치는 세그먼트가 있으면").
    english_pronoun_hint: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)


class FindingRow(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    segment_id: Mapped[str] = mapped_column(ForeignKey("segments.id"))
    category: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    original_text: Mapped[str] = mapped_column(String)
    suggested_text: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, default="llm")
    model: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String, default="pending")
    final_text: Mapped[str] = mapped_column(String, default="")
    reviewer_name: Mapped[str] = mapped_column(String, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SttCorrection(Base):
    __tablename__ = "stt_corrections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    segment_id: Mapped[str] = mapped_column(ForeignKey("segments.id"))
    original_text: Mapped[str] = mapped_column(String)
    corrected_text: Mapped[str] = mapped_column(String)
    reviewer_name: Mapped[str] = mapped_column(String)


class LearnedExample(Base):
    __tablename__ = "learned_examples"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    language: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    example: Mapped[dict] = mapped_column(JSON)


class ExportRow(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finding_count: Mapped[int] = mapped_column()
    reflection_rate: Mapped[float] = mapped_column(Float)
