"""add glossary_entries and glossary_spellings

Revision ID: 615c994a801e
Revises: e2b6a5c1f908
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '615c994a801e'
down_revision: Union[str, Sequence[str], None] = 'e2b6a5c1f908'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'glossary_entries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('title_id', sa.String(), sa.ForeignKey('titles.id'), nullable=False),
        sa.Column('korean_term', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=False),
        sa.UniqueConstraint('title_id', 'korean_term'),
    )
    op.create_table(
        'glossary_spellings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('entry_id', sa.String(), sa.ForeignKey('glossary_entries.id'), nullable=False),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('variant', sa.String(), nullable=False),
        sa.Column('canonical', sa.String(), nullable=False),
        sa.UniqueConstraint('entry_id', 'language', 'variant'),
    )


def downgrade() -> None:
    op.drop_table('glossary_spellings')
    op.drop_table('glossary_entries')
