"""add seller_score to user_acquired_sellers

Revision ID: e6f7a8b9c0d1
Revises: b3c4d5e6f7a8
Create Date: 2026-08-08 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 评分快照：grant_sellers 写入，查询已获取卖家时直接读，不再依赖 sellers.seller_score（采集时未落库）
    op.add_column(
        "user_acquired_sellers",
        sa.Column("seller_score", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_acquired_sellers", "seller_score")
