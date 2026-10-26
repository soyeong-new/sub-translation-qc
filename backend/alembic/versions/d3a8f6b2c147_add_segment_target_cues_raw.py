"""add segment target_cues_raw column

Revision ID: d3a8f6b2c147
Revises: b9e4c2a7d315
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3a8f6b2c147'
down_revision: Union[str, Sequence[str], None] = 'b9e4c2a7d315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('target_cues_raw', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('segments', 'target_cues_raw')
