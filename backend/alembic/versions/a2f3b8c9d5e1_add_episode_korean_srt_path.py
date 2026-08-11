"""add episode korean_srt_path

Revision ID: a2f3b8c9d5e1
Revises: 9031e901de3e
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2f3b8c9d5e1'
down_revision: Union[str, Sequence[str], None] = '9031e901de3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('korean_srt_path', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('episodes', 'korean_srt_path')
