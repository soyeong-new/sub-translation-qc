"""add character_merge_rejections table

Revision ID: c3e8a1d95f27
Revises: b7d2e9c4a156
Create Date: 2026-11-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e8a1d95f27'
down_revision: Union[str, Sequence[str], None] = 'b7d2e9c4a156'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'character_merge_rejections',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title_id', sa.String(), nullable=False),
        sa.Column('name_a', sa.String(), nullable=False),
        sa.Column('name_b', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['title_id'], ['titles.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('title_id', 'name_a', 'name_b'),
    )


def downgrade() -> None:
    op.drop_table('character_merge_rejections')
