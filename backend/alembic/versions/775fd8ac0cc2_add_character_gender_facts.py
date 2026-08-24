"""add character_gender_facts

Revision ID: 775fd8ac0cc2
Revises: a1d4e9f6b830
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '775fd8ac0cc2'
down_revision: Union[str, Sequence[str], None] = 'a1d4e9f6b830'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'character_gender_facts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('title_id', sa.String(), sa.ForeignKey('titles.id'), nullable=False),
        sa.Column('character_name', sa.String(), nullable=False),
        sa.Column('gender', sa.String(), nullable=False),
        sa.UniqueConstraint('title_id', 'character_name'),
    )


def downgrade() -> None:
    op.drop_table('character_gender_facts')
