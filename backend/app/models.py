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
    # STT/영상 프록시 캐시 — 같은 에피소드의 여러 target_version이 재사용하고,
    # 재시도 시에도 원본 영상 없이(최초 성공 후 삭제됨) 재분석이 가능해진다.
    stt_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    video_proxy_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 영어 SRT(선택) — 성별 확인이 필요한 줄에 대명사 힌트를 붙이는 참고
    # 자료로만 쓰인다(design §영어 SRT 대조: 자동 확정에는 쓰지 않음).
    # Episode 레벨인 이유는 한국어 영상/대상언어 SRT와 동일 — 같은 화를 여러
    # target_version(언어)으로 분석해도 참고할 영어 대사는 하나로 공유된다.
    english_srt_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 사용자가 이미 갖고 있는 한국어 SRT(선택) — 있으면 STT를 건너뛰고
    # 이 파일을 한국어 대사 소스로 쓴다(design 2026-08-11-korean-srt-input-design.md).
    # Episode 레벨인 이유는 video_path/english_srt_path와 동일 — 같은 화를
    # 여러 target_version(언어)으로 분석해도 한국어 원문은 하나로 공유된다.
    korean_srt_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class TargetVersion(Base):
    __tablename__ = "target_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    target_language: Mapped[str] = mapped_column(String)
    variant: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="analyzing")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    video_proxy_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 파이프라인 단계별 부분 실패(예: Claude 1차 교정 실패)를 사람에게 보여주기
    # 위한 목록. [{"stage": "Claude 1차 교정", "message": "..."}] 형태. 전부
    # 성공하면 None.
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # run-analysis 요청 시 받은 대상언어 SRT 업로드 경로 — 재분석("새로고침")
    # 버튼이 파일을 다시 업로드하지 않고 같은 경로로 다시 돌 수 있게 저장해둔다.
    target_srt_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # detect_global_offset()이 찾은, 한국어 STT(=영상 파일 자체의 시계)와
    # 대상언어 SRT 시계 사이 상수 초 차이. Segment.start/end는 SRT 시계를
    # 그대로 쓴다(내보내기 SRT가 원본 SRT 타이밍과 맞아야 하므로) — 그래서
    # 영상 미리보기가 올바른 장면을 보여주려면, seek할 때 프론트가
    # segment.start - video_offset_seconds로 영상 파일 자체의 시계로
    # 변환해야 한다. 오프셋이 없었으면(또는 아직 계산 전이면) None.
    video_offset_seconds: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    index: Mapped[int] = mapped_column()
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    korean_text: Mapped[str] = mapped_column(String, default="")
    target_text: Mapped[str] = mapped_column(String, default="")
    # 문법 필요성 판단(줄 단위 LLM) 결과 — 이 값이 True인 세그먼트만 사람 리뷰
    # 대상이 된다.
    gender_check_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    formality_check_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 검수자가 영상을 보고 직접 판별한 값 — 성별/격식은 서로 독립이라 한
    # 세그먼트에 둘 다 걸리면 둘 다 채워질 수 있다.
    resolved_gender_raw: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    resolved_formality_raw: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # 영어 SRT 대명사 힌트(분석 시점 계산, 참고용) — {"text", "he_count",
    # "she_count"} 형태. 영어 SRT가 없거나 겹치는 대사가 없으면 None.
    # gender_check_needed인 세그먼트에만 계산된다(design §영어 SRT 대조:
    # "걸린 줄과 시간대가 겹치는 세그먼트가 있으면").
    english_pronoun_hint: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # 한 줄에 성별이 다른 인물이 둘 이상 있을 때만 채워진다 — resolved_gender_raw
    # 하나로는 "이 줄엔 남자도 여자도 있다"를 표현할 수 없어(그 하나의 값을
    # 문장 전체에 적용하면 엉뚱한 인물까지 잘못 바뀜) 인물별 답을 리스트로
    # 따로 저장한다: [{"words":[...], "target_word_lemmas":[...], "gender":
    # "male"/"female"/"not_applicable"/None}, ...]. 채워져 있으면
    # resolved_gender_raw는 쓰지 않는다.
    resolved_gender_groups_raw: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)


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


class GenderWordResolution(Base):
    """검수자가 성별 표시 단어에 대해 실제로 어떻게 답했는지(male/female/
    not_applicable) 프로젝트를 넘어 전부 기록한다 — target_version_id가
    없다: 특정 프로젝트가 삭제돼도 이 기록은 남아야 다음 프로젝트에서 같은
    단어(기본형 기준)를 또 사람에게 물을지 판단할 수 있다.

    "제외 목록"이 아니라 "전체 이력"을 기록하는 이유: 어떤 단어가 지금까지
    not_applicable로만 판정됐고 한 번도 실제 성별로 판정된 적이 없어야만
    "이 단어는 사람과 무관하다"고 신뢰하고 자동으로 건너뛴다. 단 한 번이라도
    실제 성별(male/female)로 판정된 적이 있으면(같은 단어가 문맥에 따라
    사람을 가리킬 수 있다는 증거, 예: "grande") 절대 자동으로 건너뛰지
    않는다 — 매번 다시 물어본다(repositories.get_suggested_not_applicable_lemmas
    참고)."""
    __tablename__ = "gender_word_resolutions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    language: Mapped[str] = mapped_column(String)
    # ADJ의 기본형(lemma) — 표면형(caro/cara/caros/caras)이 달라도 같은
    # 단어로 취급한다.
    word_lemma: Mapped[str] = mapped_column(String)
    resolution: Mapped[str] = mapped_column(String)  # "male" | "female" | "not_applicable"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ExportRow(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_version_id: Mapped[str] = mapped_column(ForeignKey("target_versions.id"))
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finding_count: Mapped[int] = mapped_column()
    reflection_rate: Mapped[float] = mapped_column(Float)
