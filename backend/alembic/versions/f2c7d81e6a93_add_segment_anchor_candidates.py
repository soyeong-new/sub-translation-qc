"""add segment anchor candidates

Revision ID: f2c7d81e6a93
Revises: e1b3c9a7f204
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2c7d81e6a93'
down_revision: Union[str, Sequence[str], None] = 'e1b3c9a7f204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('gender_anchor_candidates', sa.JSON(), nullable=True))
    op.add_column('segments', sa.Column('formality_anchor_candidates', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('segments', 'formality_anchor_candidates')
    op.drop_column('segments', 'gender_anchor_candidates')
