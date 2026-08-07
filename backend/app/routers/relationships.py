"""인물 간 관계(Relationship) CRUD 및 격식 확정 엔드포인트."""

from typing import Literal, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update
from app.db import async_session
from app.models import Title, TargetVersion, Episode, Character, Relationship, Segment

router = APIRouter()


class CreateRelationshipIn(BaseModel):
    speaker_label: str
    addressee_label: str
    relationship_type: Optional[str] = None


class UpdateRelationshipIn(BaseModel):
    relationship_type: Optional[str] = None


class ConfirmFormalityIn(BaseModel):
    formality_level: Literal["formal", "informal"]


@router.get("/titles/{title_id}/relationships")
async def list_title_relationships(title_id: str):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        rows = (await session.execute(
            select(Relationship).where(Relationship.title_id == title_id)
        )).scalars().all()
        result = []
        for r in rows:
            speaker = await session.get(Character, r.speaker_character_id)
            addressee = await session.get(Character, r.addressee_character_id)
            result.append({
                "id": r.id,
                "speaker_character_id": r.speaker_character_id,
                "addressee_character_id": r.addressee_character_id,
                "speaker_label": speaker.label if speaker else None,
                "addressee_label": addressee.label if addressee else None,
                "relationship_type": r.relationship_type,
                "confirmed_formality_level": r.confirmed_formality_level,
            })
        return result


@router.post("/titles/{title_id}/relationships")
async def create_relationship(title_id: str, payload: CreateRelationshipIn):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")

        async def get_or_create(label: str) -> Character:
            existing = (await session.execute(
                select(Character).where(Character.title_id == title_id, Character.label == label)
            )).scalars().first()
            if existing is None:
                existing = Character(title_id=title_id, label=label, source="manual")
                session.add(existing)
                await session.flush()
            return existing

        speaker = await get_or_create(payload.speaker_label)
        addressee = await get_or_create(payload.addressee_label)
        rel = Relationship(title_id=title_id, speaker_character_id=speaker.id,
                           addressee_character_id=addressee.id,
                           relationship_type=payload.relationship_type)
        session.add(rel)
        await session.commit()
        return {"id": rel.id, "speaker_label": speaker.label, "addressee_label": addressee.label,
                "relationship_type": rel.relationship_type}


@router.patch("/relationships/{relationship_id}")
async def update_relationship(relationship_id: str, payload: UpdateRelationshipIn):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        if payload.relationship_type is not None:
            rel.relationship_type = payload.relationship_type
        await session.commit()
        return {"id": rel.id, "relationship_type": rel.relationship_type}


@router.delete("/relationships/{relationship_id}")
async def delete_relationship(relationship_id: str):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        # Segment.resolved_relationship_id가 relationships.id를 하드 FK로
        # 참조하므로(ondelete 없음), 이 관계로 해결된 세그먼트가 있으면 링크부터
        # 끊어야 한다 — delete_character의 동일한 패턴 참고.
        await session.execute(
            update(Segment).where(Segment.resolved_relationship_id == relationship_id)
            .values(resolved_relationship_id=None)
        )
        await session.delete(rel)
        await session.commit()
        return {"id": relationship_id, "deleted": True}


@router.post("/relationships/{relationship_id}/confirm-formality")
async def confirm_formality(relationship_id: str, payload: ConfirmFormalityIn):
    async with async_session() as session:
        rel = await session.get(Relationship, relationship_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        rel.confirmed_formality_level = payload.formality_level
        await session.commit()
        return {"id": rel.id, "confirmed_formality_level": rel.confirmed_formality_level}


@router.get("/target-versions/{target_version_id}/relationships")
async def list_relationships(target_version_id: str):
    """관계도 인물과 마찬가지로 title 단위로 공유된다. 화자/상대 인물의 라벨을 함께
    내려줘야 검수자가 관계를 식별할 수 있으므로(관계 ID만으로는 누구와 누구의 관계인지
    알 수 없음), 각 관계마다 Character를 조회해 라벨을 붙인다."""
    async with async_session() as session:
        tv = await session.get(TargetVersion, target_version_id)
        if tv is None:
            raise HTTPException(404, "target version not found")
        episode = await session.get(Episode, tv.episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        rows = (await session.execute(
            select(Relationship).where(Relationship.title_id == episode.title_id)
        )).scalars().all()
        result = []
        for r in rows:
            speaker = await session.get(Character, r.speaker_character_id)
            addressee = await session.get(Character, r.addressee_character_id)
            result.append({
                "id": r.id,
                "speaker_character_id": r.speaker_character_id,
                "addressee_character_id": r.addressee_character_id,
                "speaker_label": speaker.label if speaker else None,
                "addressee_label": addressee.label if addressee else None,
                "confirmed_formality_level": r.confirmed_formality_level,
            })
        return result
