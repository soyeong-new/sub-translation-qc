"""drop character/relationship roster

인물/관계 로스터(인물관계도 이미지 업로드 → vision 추출 → 확정)와 그에 연결된
세그먼트 해결 필드를 전부 없앤다. 화자를 텍스트만으로 특정할 근거가 없어
로스터를 검사/교정에 실질적으로 연결할 방법이 없었고(앵커매칭도 후보 제시
수준), 검수자가 세그먼트별로 영상을 보고 직접 성별/격식을 판별하는 방식
(resolved_gender_raw/resolved_formality_raw)만 남긴다.

Revision ID: c8f4a1d9b3e6
Revises: a7d2e9f14c68
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f4a1d9b3e6'
down_revision: Union[str, Sequence[str], None] = 'a7d2e9f14c68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # e1b3c9a7f204가 이 제약을 만들 때 명시한 이름 그대로 드롭해야 한다.
    # 예전엔 여기서 Postgres 자동 명명 규칙(<table>_<column>_fkey)을 썼는데,
    # 그건 개발 DB가 순수 alembic 이력이 아니라 SQLAlchemy create_all로 세팅된
    # 이력이 섞여 있어 우연히 맞았던 것뿐이다 — alembic만으로 처음부터
    # 새로 세팅하는 DB(배포용 등)에서는 e1b3c9a7f204가 만든 이름
    # (fk_segments_resolved_*)만 존재해서, 자동 명명 규칙으로 드롭하면
    # UndefinedObject 에러로 마이그레이션 체인 전체가 실패한다.
    op.drop_constraint('fk_segments_resolved_relationship_id', 'segments', type_='foreignkey')
    op.drop_constraint('fk_segments_resolved_character_id', 'segments', type_='foreignkey')
    op.drop_column('segments', 'formality_anchor_candidates')
    op.drop_column('segments', 'gender_anchor_candidates')
    op.drop_column('segments', 'resolved_relationship_id')
    op.drop_column('segments', 'resolved_character_id')
    op.drop_table('relationships')
    op.drop_column('titles', 'chart_extraction_error')
    op.drop_column('titles', 'chart_extraction_status')
    op.drop_column('titles', 'chart_image_path')
    op.drop_table('characters')


def downgrade() -> None:
    op.create_table('characters',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title_id', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('confirmed_gender', sa.String(), nullable=True),
        sa.Column('suggested_gender', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['title_id'], ['titles.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column('titles', sa.Column('chart_image_path', sa.String(), nullable=True))
    op.add_column('titles', sa.Column('chart_extraction_status', sa.String(),
                                       nullable=False, server_default='none'))
    op.add_column('titles', sa.Column('chart_extraction_error', sa.String(), nullable=True))
    op.create_table('relationships',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title_id', sa.String(), nullable=False),
        sa.Column('speaker_character_id', sa.String(), nullable=False),
        sa.Column('addressee_character_id', sa.String(), nullable=False),
        sa.Column('confirmed_formality_level', sa.String(), nullable=True),
        sa.Column('relationship_type', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['addressee_character_id'], ['characters.id'], ),
        sa.ForeignKeyConstraint(['speaker_character_id'], ['characters.id'], ),
        sa.ForeignKeyConstraint(['title_id'], ['titles.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column('segments', sa.Column('resolved_character_id', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('resolved_relationship_id', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('gender_anchor_candidates', sa.JSON(), nullable=True))
    op.add_column('segments', sa.Column('formality_anchor_candidates', sa.JSON(), nullable=True))
    op.create_foreign_key('fk_segments_resolved_character_id', 'segments', 'characters',
                           ['resolved_character_id'], ['id'])
    op.create_foreign_key('fk_segments_resolved_relationship_id', 'segments', 'relationships',
                           ['resolved_relationship_id'], ['id'])
