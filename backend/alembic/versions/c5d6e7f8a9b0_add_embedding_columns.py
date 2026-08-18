"""add embedding columns to sellers/products

Revision ID: c5d6e7f8a9b0
Revises: e6f7a8b9c0d1
Create Date: 2026-08-18

RAG 语义检索：pgvector 扩展 + vector(1024) 列（bge-m3）+ HNSW 余弦索引。
存量行为 NULL，由 backend/scripts/backfill_embeddings.py 回填。
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE sellers ADD COLUMN embedding vector(1024)")
    op.execute("ALTER TABLE products ADD COLUMN embedding vector(1024)")
    op.execute(
        "CREATE INDEX ix_sellers_embedding ON sellers USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_products_embedding ON products USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_embedding")
    op.execute("DROP INDEX IF EXISTS ix_sellers_embedding")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE sellers DROP COLUMN IF EXISTS embedding")
