"""add episode stt cache and target_version warnings

Revision ID: b1e4a7c92f31
Revises: a0cb0e08d44d
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1e4a7c92f31'
down_revision: Union[str, Sequence[str], None] = 'a0cb0e08d44d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('stt_cache', sa.JSON(), nullable=True))
    op.add_column('episodes', sa.Column('video_proxy_path', sa.String(), nullable=True))
    op.add_column('target_versions', sa.Column('warnings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('target_versions', 'warnings')
    op.drop_column('episodes', 'video_proxy_path')
    op.drop_column('episodes', 'stt_cache')
