"""add english srt fields

Revision ID: a7d2e9f14c68
Revises: f2c7d81e6a93
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d2e9f14c68'
down_revision: Union[str, Sequence[str], None] = 'f2c7d81e6a93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('english_srt_path', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('english_pronoun_hint', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('segments', 'english_pronoun_hint')
    op.drop_column('episodes', 'english_srt_path')
