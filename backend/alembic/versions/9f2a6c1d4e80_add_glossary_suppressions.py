"""add glossary suppressions

Revision ID: 9f2a6c1d4e80
Revises: 615c994a801e
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f2a6c1d4e80"
down_revision: Union[str, Sequence[str], None] = "615c994a801e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "glossary_suppressions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title_id", sa.String(), nullable=False),
        sa.Column("korean_term", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title_id", "korean_term"),
    )


def downgrade() -> None:
    op.drop_table("glossary_suppressions")
