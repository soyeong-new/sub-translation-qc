"""add target_version video_proxy_path column

Revision ID: a1f3c9d2e087
Revises: 96a070c6af64
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c9d2e087'
down_revision: Union[str, Sequence[str], None] = '96a070c6af64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('target_versions', sa.Column('video_proxy_path', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('target_versions', 'video_proxy_path')
