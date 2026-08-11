"""add target_version target_srt_path

Revision ID: d3f6a2b8c941
Revises: c8f4a1d9b3e6
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f6a2b8c941'
down_revision: Union[str, Sequence[str], None] = 'c8f4a1d9b3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('target_versions', sa.Column('target_srt_path', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('target_versions', 'target_srt_path')
