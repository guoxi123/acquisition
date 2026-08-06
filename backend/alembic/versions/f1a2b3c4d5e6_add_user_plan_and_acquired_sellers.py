"""add user plan and acquired sellers

Revision ID: f1a2b3c4d5e6
Revises: 01f306de7a98
Create Date: 2026-08-06 11:00:00.000000

扩展等级（intermediate/advanced）时需手写（alembic autogenerate 检测不到 enum 值新增）：
1) op.execute("ALTER TYPE user_plan ADD VALUE IF NOT EXISTS 'intermediate'")
2) backend/app/quota.py 的 PLAN_MONTHLY_QUOTA 加一项。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '01f306de7a98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先建 PG enum 类型（op.add_column 不会自动创建，与 create_table 不同，需显式 CREATE TYPE）
    op.execute("CREATE TYPE user_plan AS ENUM ('free', 'basic')")
    op.add_column(
        'users',
        sa.Column(
            'plan',
            postgresql.ENUM('free', 'basic', name='user_plan', create_type=False),
            server_default=sa.text("'free'"),
            nullable=False,
        ),
    )
    op.add_column(
        'users',
        sa.Column('plan_expires_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'user_acquired_sellers',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('seller_id', sa.String(length=30), nullable=False),
        sa.Column(
            'acquired_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['seller_id'], ['sellers.seller_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'seller_id', name='uq_user_seller'),
    )
    op.create_index(
        op.f('ix_user_acquired_sellers_acquired_at'),
        'user_acquired_sellers',
        ['acquired_at'],
        unique=False,
    )
    op.create_index(
        op.f('ix_user_acquired_sellers_seller_id'),
        'user_acquired_sellers',
        ['seller_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_user_acquired_sellers_user_id'),
        'user_acquired_sellers',
        ['user_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_user_acquired_sellers_user_id'), table_name='user_acquired_sellers'
    )
    op.drop_index(
        op.f('ix_user_acquired_sellers_seller_id'), table_name='user_acquired_sellers'
    )
    op.drop_index(
        op.f('ix_user_acquired_sellers_acquired_at'), table_name='user_acquired_sellers'
    )
    op.drop_table('user_acquired_sellers')
    op.drop_column('users', 'plan_expires_at')
    op.drop_column('users', 'plan')
    op.execute("DROP TYPE user_plan")
