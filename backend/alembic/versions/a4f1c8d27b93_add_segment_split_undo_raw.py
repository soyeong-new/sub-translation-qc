"""add segment split_undo_raw column

Revision ID: a4f1c8d27b93
Revises: d3a8f6b2c147
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4f1c8d27b93'
down_revision: Union[str, Sequence[str], None] = 'd3a8f6b2c147'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('split_undo_raw', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('segments', 'split_undo_raw')
