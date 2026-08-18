"""add title deleted_at (soft delete)

Revision ID: a1d4e9f6b830
Revises: c7f92a4e1b56
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1d4e9f6b830'
down_revision: Union[str, Sequence[str], None] = 'c7f92a4e1b56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('titles', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('titles', 'deleted_at')
