"""add segment resolution fields

Revision ID: e1b3c9a7f204
Revises: d4a8f13c7b56
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1b3c9a7f204'
down_revision: Union[str, Sequence[str], None] = 'd4a8f13c7b56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('gender_check_needed', sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    op.add_column('segments', sa.Column('formality_check_needed', sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    op.add_column('segments', sa.Column('resolved_character_id', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('resolved_gender_raw', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('resolved_relationship_id', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('resolved_formality_raw', sa.String(), nullable=True))
    op.create_foreign_key('fk_segments_resolved_character_id', 'segments', 'characters',
                           ['resolved_character_id'], ['id'])
    op.create_foreign_key('fk_segments_resolved_relationship_id', 'segments', 'relationships',
                           ['resolved_relationship_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_segments_resolved_relationship_id', 'segments', type_='foreignkey')
    op.drop_constraint('fk_segments_resolved_character_id', 'segments', type_='foreignkey')
    op.drop_column('segments', 'resolved_formality_raw')
    op.drop_column('segments', 'resolved_relationship_id')
    op.drop_column('segments', 'resolved_gender_raw')
    op.drop_column('segments', 'resolved_character_id')
    op.drop_column('segments', 'formality_check_needed')
    op.drop_column('segments', 'gender_check_needed')
