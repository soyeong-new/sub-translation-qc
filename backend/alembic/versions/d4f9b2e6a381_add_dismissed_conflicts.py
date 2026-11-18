"""add dismissed_conflicts table

Revision ID: d4f9b2e6a381
Revises: c3e8a1d95f27
Create Date: 2026-11-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f9b2e6a381'
down_revision: Union[str, Sequence[str], None] = 'c3e8a1d95f27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dismissed_conflicts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['title_id'], ['titles.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('title_id', 'kind', 'key'),
    )


def downgrade() -> None:
    op.drop_table('dismissed_conflicts')
