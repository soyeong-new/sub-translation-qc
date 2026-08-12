"""drop english srt fields and gender_word_resolutions table

Revision ID: b4e8f21ac763
Revises: a2f3b8c9d5e1
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8f21ac763'
down_revision: Union[str, Sequence[str], None] = 'a2f3b8c9d5e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('gender_word_resolutions')
    op.drop_column('segments', 'english_pronoun_hint')
    op.drop_column('episodes', 'english_srt_path')


def downgrade() -> None:
    op.add_column('episodes', sa.Column('english_srt_path', sa.String(), nullable=True))
    op.add_column('segments', sa.Column('english_pronoun_hint', sa.JSON(), nullable=True))
    op.create_table(
        'gender_word_resolutions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('word_lemma', sa.String(), nullable=False),
        sa.Column('resolution', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
