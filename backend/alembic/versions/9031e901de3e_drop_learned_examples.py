"""drop learned_examples

learned_examples 테이블은 초기 스키마부터 존재했지만 실제로 row를 넣는 코드가
프로젝트 어디에도 없었다(데드 테이블) — 검수 이력 학습은 GenderWordResolution
테이블로 다른 방식으로 이미 구현돼 있다.

Revision ID: 9031e901de3e
Revises: 82a4f0584f01
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9031e901de3e'
down_revision: Union[str, Sequence[str], None] = '82a4f0584f01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('learned_examples')


def downgrade() -> None:
    op.create_table('learned_examples',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('target_version_id', sa.String(), nullable=False),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('example', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['target_version_id'], ['target_versions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
