"""add target_version deleted_at (soft delete)

Revision ID: e2b6a5c1f908
Revises: 775fd8ac0cc2
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2b6a5c1f908'
down_revision: Union[str, Sequence[str], None] = '775fd8ac0cc2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('target_versions', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('target_versions', 'deleted_at')
