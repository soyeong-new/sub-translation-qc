"""등장인물(Character) CRUD 및 성별 확정 엔드포인트."""

from typing import Literal, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, delete, update
from app.db import async_session
from app.models import Title, TargetVersion, Episode, Character, Relationship, Segment

router = APIRouter()


class CreateCharacterIn(BaseModel):
    label: str
    suggested_gender: Optional[Literal["male", "female"]] = None


class UpdateCharacterIn(BaseModel):
    label: Optional[str] = None
    suggested_gender: Optional[Literal["male", "female"]] = None


class ConfirmGenderIn(BaseModel):
    gender: Literal["male", "female"]


@router.get("/titles/{title_id}/characters")
async def list_title_characters(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        rows = (await session.execute(
            select(Character).where(Character.title_id == title_id)
        )).scalars().all()
        return [
            {"id": c.id, "label": c.label, "confirmed_gender": c.confirmed_gender,
             "suggested_gender": c.suggested_gender, "source": c.source}
            for c in rows
        ]


@router.post("/titles/{title_id}/characters")
async def create_character(title_id: str, payload: CreateCharacterIn):
    label = payload.label.strip()
    if not label:
        raise HTTPException(400, "인물 이름은 비어 있을 수 없습니다.")
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        # create_relationship의 get_or_create와 동일한 패턴: 같은 title 안에서
        # label이 중복되는 Character가 생기면, label로 조회하는 다른 코드 경로
        # (create_relationship, save_chart_extraction_result,
        # _save_characters_and_relationships)가 어느 행에 묶일지 예측할 수 없게
        # 된다. 이미 같은 이름이 있으면 새로 만들지 않고 그 행을 재사용한다 —
        # 프런트엔드에 409 처리 코드가 없으므로 UI의 "인물 추가"가 그대로
        # idempotent하게 동작하게 한다.
        existing = (await session.execute(
            select(Character).where(Character.title_id == title_id, Character.label == label)
        )).scalars().first()
        if existing is not None:
            if payload.suggested_gender is not None:
                existing.suggested_gender = payload.suggested_gender
                await session.commit()
            return {"id": existing.id, "label": existing.label,
                    "suggested_gender": existing.suggested_gender}
        char = Character(title_id=title_id, label=label,
                         suggested_gender=payload.suggested_gender, source="manual")
        session.add(char)
        await session.commit()
        return {"id": char.id, "label": char.label, "suggested_gender": char.suggested_gender}


@router.patch("/characters/{character_id}")
async def update_character(character_id: str, payload: UpdateCharacterIn):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        if payload.label is not None:
            label = payload.label.strip()
            if not label:
                raise HTTPException(400, "인물 이름은 비어 있을 수 없습니다.")
            char.label = label
        if payload.suggested_gender is not None:
            char.suggested_gender = payload.suggested_gender
        await session.commit()
        return {"id": char.id, "label": char.label, "suggested_gender": char.suggested_gender}


@router.delete("/characters/{character_id}")
async def delete_character(character_id: str):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        # Segment.resolved_character_id도 characters.id를 하드 FK로 참조한다
        # (ondelete 없음) — 이 인물로 해결된 세그먼트가 있으면 그 링크부터
        # 끊어야 한다. 세그먼트 자체나 다른 필드는 그대로 두고 resolved_character_id만
        # NULL로 되돌린다 — "해결된 대상이 사라졌으니 다시 미해결 상태로" 되는 게
        # 맞는 동작이다(gender_check_needed 등 플래그는 그대로 유지돼 다시 검수 대상이 됨).
        await session.execute(
            update(Segment).where(Segment.resolved_character_id == character_id)
            .values(resolved_character_id=None)
        )
        # 이 인물이 관련된 Relationship들도 곧 지워질 텐데, 그 관계로 해결된
        # 세그먼트가 있다면 Segment.resolved_relationship_id가 매달린 채 남아
        # 같은 FK 문제를 일으킨다 — 관계를 지우기 전에 먼저 링크를 끊는다.
        await session.execute(
            update(Segment).where(
                Segment.resolved_relationship_id.in_(
                    select(Relationship.id).where(
                        (Relationship.speaker_character_id == character_id) |
                        (Relationship.addressee_character_id == character_id)
                    )
                )
            ).values(resolved_relationship_id=None)
        )
        # Relationship이 characters.id를 하드 FK로 참조하므로(ondelete 없음),
        # 인물을 지우기 전에 그 인물이 관련된 관계부터 지워야 한다.
        await session.execute(
            delete(Relationship).where(
                (Relationship.speaker_character_id == character_id) |
                (Relationship.addressee_character_id == character_id)
            )
        )
        await session.delete(char)
        await session.commit()
        return {"id": character_id, "deleted": True}


@router.post("/characters/{character_id}/confirm-gender")
async def confirm_gender(character_id: str, payload: ConfirmGenderIn):
    async with async_session() as session:
        char = await session.get(Character, character_id)
        if char is None:
            raise HTTPException(404, "character not found")
        char.confirmed_gender = payload.gender
        await session.commit()
        return {"id": char.id, "confirmed_gender": char.confirmed_gender}


@router.get("/target-versions/{target_version_id}/characters")
async def list_characters(target_version_id: str):
    """인물은 target_version이 아니라 title 단위로 공유된다 (Task 18 confirm-gender와
    동일한 전역 제약: 같은 작품의 에피소드/언어 전반에서 재사용). target_version_id →
    episode_id → title_id 체인을 따라가 해당 title의 인물 목록을 반환한다."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        rows = (await session.execute(
            select(Character).where(Character.title_id == episode.title_id)
        )).scalars().all()
        return [
            {"id": c.id, "label": c.label, "confirmed_gender": c.confirmed_gender}
            for c in rows
        ]
