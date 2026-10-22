"""add glossary candidates and spelling is_new

Revision ID: b9e4c2a7d315
Revises: 9f2a6c1d4e80
Create Date: 2026-10-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9e4c2a7d315'
down_revision: Union[str, Sequence[str], None] = '9f2a6c1d4e80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 행은 모두 "신규 아님"으로 시작한다(server_default=false로 채움).
    op.add_column('glossary_entries', sa.Column('candidates', sa.JSON(), nullable=True))
    op.add_column('glossary_spellings', sa.Column(
        'is_new', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('glossary_spellings', 'is_new')
    op.drop_column('glossary_entries', 'candidates')
