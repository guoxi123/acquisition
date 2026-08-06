"""add session title

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-06 16:00:00.000000

会话重命名：memory_sessions 加 title 列（自定义标题；为空时取首条用户消息）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('memory_sessions', sa.Column('title', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('memory_sessions', 'title')
