"""add phone and sms_codes

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-06 15:00:00.000000

注册手机短信验证（阿里云 SMS）：User 加 phone（手机号登录），新建 sms_codes 表（验证码 5 分钟 TTL）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('phone', sa.String(length=20), nullable=True))
    op.create_index('ix_users_phone', 'users', ['phone'], unique=True)

    op.create_table(
        'sms_codes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('code', sa.String(length=6), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sms_codes_phone', 'sms_codes', ['phone'])
    op.create_index('ix_sms_codes_expires_at', 'sms_codes', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_sms_codes_expires_at', table_name='sms_codes')
    op.drop_index('ix_sms_codes_phone', table_name='sms_codes')
    op.drop_table('sms_codes')
    op.drop_index('ix_users_phone', table_name='users')
    op.drop_column('users', 'phone')
