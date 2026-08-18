"""存量数据 embedding 回填（一次性手动脚本）。

用法（backend/ 目录下）：
    .venv/bin/python scripts/backfill_embeddings.py --table sellers
    .venv/bin/python scripts/backfill_embeddings.py --table products

分页游标扫 embedding IS NULL 的行，批量调 SiliconFlow /embeddings 后 UPDATE。
失败的批次跳过并打印，可重跑（幂等：只碰 NULL 行）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.embedding import embed_texts, product_text, seller_text
from app.models.product import Product
from app.models.seller import Seller

BATCH = 200


async def run(table: str) -> None:
    model, text_fn, pk = (Seller, seller_text, Seller.seller_id) if table == "sellers" else (
        Product, product_text, Product.asin
    )
    engine = create_async_engine(settings.database_url)
    done = 0
    failed = 0
    try:
        while True:
            async with AsyncSession(engine) as db:
                rows = (
                    await db.execute(
                        select(model)
                        .where(model.embedding.is_(None))
                        .order_by(pk)
                        .limit(BATCH)
                    )
                ).scalars().all()
                if not rows:
                    break
                texts = [text_fn(r) for r in rows]
                empty = [i for i, t in enumerate(texts) if not t.strip()]
                try:
                    embs = await embed_texts(texts)
                except Exception as e:
                    failed += len(rows)
                    print(f"批次失败（{[getattr(r, pk.name) for r in rows][:5]}…）: {e}")
                    # 该批全跳过会死循环，标记空文本外的行退出：直接 break 让人工处理
                    break
                for r, t, e in zip(rows, texts, embs):
                    r.embedding = e if t.strip() else None
                await db.commit()
                done += len(rows) - len(empty)
                if empty:
                    # 无文本的行永远补不了 embedding，写零向量占位避免反复扫描
                    for i in empty:
                        rows[i].embedding = [0.0] * settings.embedding_dim
                    await db.commit()
                print(f"进度: {done} 行已回填")
    finally:
        await engine.dispose()
    print(f"完成: {done} 回填, {failed} 失败")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", choices=["sellers", "products"], required=True)
    asyncio.run(run(parser.parse_args().table))
