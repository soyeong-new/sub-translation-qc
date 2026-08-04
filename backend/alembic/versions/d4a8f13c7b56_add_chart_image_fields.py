"""add character chart image fields

Revision ID: d4a8f13c7b56
Revises: b1e4a7c92f31
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a8f13c7b56'
down_revision: Union[str, Sequence[str], None] = 'b1e4a7c92f31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('titles', sa.Column('chart_image_path', sa.String(), nullable=True))
    op.add_column('titles', sa.Column('chart_extraction_status', sa.String(),
                                       nullable=False, server_default='none'))
    op.add_column('titles', sa.Column('chart_extraction_error', sa.String(), nullable=True))
    op.add_column('characters', sa.Column('suggested_gender', sa.String(), nullable=True))
    op.add_column('characters', sa.Column('source', sa.String(), nullable=True))
    op.add_column('relationships', sa.Column('relationship_type', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('relationships', 'relationship_type')
    op.drop_column('characters', 'source')
    op.drop_column('characters', 'suggested_gender')
    op.drop_column('titles', 'chart_extraction_error')
    op.drop_column('titles', 'chart_extraction_status')
    op.drop_column('titles', 'chart_image_path')
