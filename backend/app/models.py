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


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    episode_no: Mapped[int | None] = mapped_column(nullable=True)
    video_path: Mapped[str] = mapped_column(String)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    label: Mapped[str] = mapped_column(String)
    confirmed_gender: Mapped[str | None] = mapped_column(String, nullable=True)


class Relationship(Base):
    __tablename__ = "relationships"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    speaker_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"))
    addressee_character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"))
    confirmed_formality_level: Mapped[str | None] = mapped_column(String, nullable=True)


class TargetVersion(Base):
    __tablename__ = "target_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    target_language: Mapped[str] = mapped_column(String)
    variant: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="analyzing")


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    index: Mapped[int] = mapped_column()
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    korean_text: Mapped[str] = mapped_column(String, default="")
    target_text: Mapped[str] = mapped_column(String, default="")


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
