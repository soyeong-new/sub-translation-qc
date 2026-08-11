"""add segment resolved gender groups

Revision ID: 82a4f0584f01
Revises: f7c1e8a2d5b3
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '82a4f0584f01'
down_revision: Union[str, Sequence[str], None] = 'f7c1e8a2d5b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('resolved_gender_groups_raw', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('segments', 'resolved_gender_groups_raw')
