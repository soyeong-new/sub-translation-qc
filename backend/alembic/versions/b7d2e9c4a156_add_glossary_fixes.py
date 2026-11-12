"""add glossary_fixes table

Revision ID: b7d2e9c4a156
Revises: a4f1c8d27b93
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d2e9c4a156'
down_revision: Union[str, Sequence[str], None] = 'a4f1c8d27b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'glossary_fixes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('target_version_id', sa.String(), nullable=False),
        sa.Column('segment_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('korean_term', sa.String(), nullable=False),
        sa.Column('before_text', sa.String(), nullable=False),
        sa.Column('after_text', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['target_version_id'], ['target_versions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('target_version_id', 'segment_id', 'kind', 'korean_term'),
    )


def downgrade() -> None:
    op.drop_table('glossary_fixes')
