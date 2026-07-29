"""파이프라인 실행 결과(세그먼트/findings/인물/관계)를 DB에 영속화하는 모듈."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    Character, Episode, FindingRow, Relationship, Segment, TargetVersion,
)
from app.schemas import Finding


def _ns(target_version_id: str, local_id: str) -> str:
    """파이프라인 내부 ID(예: "pair_1", "finding_pair_1_translation")를
    target_version 단위로 네임스페이싱한다.

    alignment.align()은 실행마다 1부터 다시 세는 카운터로 pair.id를 만들기
    때문에("pair_1", "pair_2", ...) 서로 다른 target_version의 결과를 같은 DB에
    저장하면 segments/findings의 전역 PK가 충돌한다. ID 생성 규칙 자체는
    파이프라인 한 번의 실행 안에서만 쓰이므로 그대로 두고, 영속화 시점에만
    prefix를 붙여 전역 유일성을 확보한다."""
    return f"{target_version_id}:{local_id}"


async def save_pipeline_result(session: AsyncSession, target_version_id: str,
                                result: dict) -> None:
    # segments 먼저: findings.segment_id가 segments.id를 참조하는 FK이므로,
    # 네임스페이싱된 pair.id를 Segment.id로 써서 findings가 참조할 수 있게 한다.
    for index, pair in enumerate(result["pairs"]):
        if pair.target is not None:
            start, end = pair.target.start, pair.target.end
        elif pair.korean is not None:
            start, end = pair.korean.start, pair.korean.end
        else:
            start, end = 0.0, 0.0
        session.add(Segment(
            id=_ns(target_version_id, pair.id), target_version_id=target_version_id,
            index=index, start=start, end=end,
            korean_text=pair.korean.text if pair.korean else "",
            target_text=pair.target.text if pair.target else "",
        ))

    # 명시적 flush: segments를 findings보다 먼저 INSERT해야
    # findings.segment_id의 FK 제약이 통과한다 (두 모델 간 relationship()이
    # 없어 세션의 자동 의존성 정렬만으로는 순서가 보장되지 않았다).
    await session.flush()

    for f in result["findings"]:
        session.add(FindingRow(
            id=_ns(target_version_id, f.id), target_version_id=target_version_id,
            segment_id=_ns(target_version_id, f.segment_id),
            category=f.category, description=f.description,
            original_text=f.original_text, suggested_text=f.suggested_text,
            confidence=f.confidence, source=f.source, status=f.status,
        ))

    # 포맷 위반은 FormatViolation(스키마)이라 Finding과 필드가 1:1로 대응하지
    # 않는다. category="formatting" FindingRow로 변환해 검수 UI가 다른 카테고리와
    # 동일하게 다룰 수 있게 한다. 한 세그먼트에서 ellipsis/line_length가 동시에
    # 발생할 수 있으므로(온점 보정 후 줄 길이를 다시 재기 때문) id에 rule을 포함해
    # PK 충돌을 막는다.
    target_text_by_pair = {
        p.id: (p.target.text if p.target else "") for p in result["pairs"]
    }
    for v in result.get("format_violations", []):
        # 자동보정된 위반(온점 4개 이상)은 판단 여지가 없는 기계적 규칙이라
        # 파이프라인이 이미 텍스트에 적용해 놓은 상태다. 검수자가 결정할 것이
        # 남아 있지 않으므로 대기열에 쌓아 두지 않고 바로 approved로 확정한다.
        # 반면 줄 길이 위반은 의미를 보존하며 문장을 줄이는 판단이 필요하므로
        # (format_rules.check_line_length가 자동 수정을 하지 않는 이유와 동일)
        # pending으로 남겨 검수자에게 넘긴다.
        suggested_text = v.fixed_text if v.auto_fixed else ""
        session.add(FindingRow(
            id=_ns(target_version_id, f"finding_{v.segment_id}_formatting_{v.rule}"),
            target_version_id=target_version_id,
            segment_id=_ns(target_version_id, v.segment_id),
            category="formatting", description=v.detail,
            original_text=target_text_by_pair.get(v.segment_id, ""),
            suggested_text=suggested_text,
            confidence=1.0, source="rule",
            status="approved" if v.auto_fixed else "pending",
            final_text=suggested_text if v.auto_fixed else "",
        ))

    await _save_characters_and_relationships(session, target_version_id, result)


async def _resolve_title_id(session: AsyncSession, target_version_id: str) -> Optional[str]:
    """target_version_id → episode_id → title_id. 인물/관계는 target_version이
    아니라 title 단위로 공유되므로(design §6의 전역 제약: 같은 작품의 에피소드/
    언어 버전 전반에서 재사용) 저장 전에 title_id를 알아내야 한다."""
    tv = await session.get(TargetVersion, target_version_id)
    if tv is None:
        return None
    episode = await session.get(Episode, tv.episode_id)
    return episode.title_id if episode is not None else None


async def _save_characters_and_relationships(session: AsyncSession,
                                              target_version_id: str,
                                              result: dict) -> None:
    """인물/관계를 title 단위로 누적 저장한다.

    같은 작품의 다른 화를 분석해도 동일 인물이 중복 생성되면 안 되므로
    (title_id, label) 기준으로 기존 행을 먼저 찾고 없을 때만 새로 만든다.
    gender_questions/register_questions는 별도 저장이 필요 없다 —
    "confirmed_gender IS NULL"인 Character 자체가 확인 필요 신호이며,
    list_characters/프론트엔드(ReviewView)도 그 값의 null 여부로 판단한다."""
    characters = result.get("characters") or []
    relationships = result.get("relationships") or []
    if not characters and not relationships:
        return
    title_id = await _resolve_title_id(session, target_version_id)
    if title_id is None:
        return

    char_by_label: dict[str, Character] = {}

    async def get_or_create_character(label: Optional[str]) -> Optional[Character]:
        if not label:
            return None
        if label not in char_by_label:
            existing = (await session.execute(
                select(Character).where(Character.title_id == title_id,
                                        Character.label == label)
            )).scalars().first()
            if existing is None:
                existing = Character(title_id=title_id, label=label)
                session.add(existing)
            char_by_label[label] = existing
        return char_by_label[label]

    for c in characters:
        await get_or_create_character(c.get("label"))
    await session.flush()  # 관계가 참조할 characters.id를 먼저 확정한다.

    for r in relationships:
        # 인물 목록에 없는 라벨을 관계가 참조할 수도 있으므로(다른 화에서 이미
        # 등록된 인물 등) 여기서도 get-or-create로 해석한다.
        speaker = await get_or_create_character(r.get("speaker_label"))
        addressee = await get_or_create_character(r.get("addressee_label"))
        if speaker is None or addressee is None:
            continue
        await session.flush()
        existing = (await session.execute(
            select(Relationship).where(
                Relationship.title_id == title_id,
                Relationship.speaker_character_id == speaker.id,
                Relationship.addressee_character_id == addressee.id,
            )
        )).scalars().first()
        if existing is None:
            session.add(Relationship(
                title_id=title_id, speaker_character_id=speaker.id,
                addressee_character_id=addressee.id,
            ))
    await session.flush()


async def get_findings(session: AsyncSession, target_version_id: str) -> List[FindingRow]:
    rows = await session.execute(
        select(FindingRow).where(FindingRow.target_version_id == target_version_id)
    )
    return list(rows.scalars().all())
