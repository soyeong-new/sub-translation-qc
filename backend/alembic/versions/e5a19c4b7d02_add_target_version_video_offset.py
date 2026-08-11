"""add target_version video_offset_seconds

Revision ID: e5a19c4b7d02
Revises: d3f6a2b8c941
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a19c4b7d02'
down_revision: Union[str, Sequence[str], None] = 'd3f6a2b8c941'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('target_versions', sa.Column('video_offset_seconds', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('target_versions', 'video_offset_seconds')
