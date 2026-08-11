"""add gender_word_resolutions table

Revision ID: f7c1e8a2d5b3
Revises: e5a19c4b7d02
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c1e8a2d5b3'
down_revision: Union[str, Sequence[str], None] = 'e5a19c4b7d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gender_word_resolutions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('word_lemma', sa.String(), nullable=False),
        sa.Column('resolution', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('gender_word_resolutions')
