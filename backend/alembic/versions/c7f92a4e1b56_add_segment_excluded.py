"""add segment excluded column

Revision ID: c7f92a4e1b56
Revises: b4e8f21ac763
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f92a4e1b56'
down_revision: Union[str, Sequence[str], None] = 'b4e8f21ac763'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('segments', sa.Column('excluded', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('segments', 'excluded')
