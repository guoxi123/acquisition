"""语义检索集成测试：真实 PG + pgvector，固定 embedding 验证排序与工具。"""

import uuid

import pytest
from sqlalchemy import delete, select, text

from app.agent.skills import semantic_search_sellers
from app.core.db import async_session
from app.models.seller import Seller
from app.models.user import User
from app.models.user_acquired_seller import UserAcquiredSeller

PREFIX = "semtest_"


def _vec(first: float) -> list[float]:
    return [first] + [0.0] * 1023


@pytest.fixture(autouse=True)
async def ensure_vector_extension():
    """conftest 的建表不覆盖 extension，这里补建 + 清理前缀数据。"""
    async with async_session() as db:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await db.commit()
    yield
    async with async_session() as db:
        await db.execute(delete(Seller).where(Seller.seller_id.startswith(PREFIX)))
        await db.execute(delete(User).where(User.username.startswith(PREFIX)))
        await db.commit()


@pytest.mark.asyncio
async def test_cosine_ordering_and_tool():
    """3 个固定向量卖家：查 [1,0,...] 应按 first 降序召回（余弦距离升序）。"""
    from app.auth.security import hash_password

    uid = uuid.uuid4()
    uname = f"{PREFIX}{uid.hex[:8]}"
    async with async_session() as db:
        user = User(username=uname, password_hash=hash_password("x-test-pass-123"))
        db.add(user)
        await db.flush()
        # 固定向量：first 越大与查询 [1,0..] 余弦相似度越高
        for i, first in enumerate([0.9, 0.5, 0.1]):
            sid = f"{PREFIX}{uid.hex[:8]}_{i}"
            db.add(
                Seller(
                    seller_id=sid,
                    name=f"Sem Test Seller {i}",
                    category="kitchen",
                    marketplace="amazon.com",
                    embedding=_vec(first),
                )
            )
            db.add(
                UserAcquiredSeller(
                    user_id=user.id, seller_id=sid, seller_score=50 + i
                )
            )
        await db.commit()

    # 直接 SQL 验证 <=> 排序
    async with async_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT seller_id FROM sellers WHERE seller_id LIKE :p "
                    "AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> :v"
                ),
                {"p": f"{PREFIX}%", "v": str(_vec(1.0))},
            )
        ).scalars().all()
        assert len(rows) == 3
        assert rows[0].endswith("_0") and rows[1].endswith("_1") and rows[2].endswith("_2")

    # 工具层：semantic_search_sellers（mock embedding 为查询向量）
    from unittest.mock import AsyncMock

    from app.core import embedding as emb_mod

    orig = emb_mod.embed_texts
    emb_mod.embed_texts = AsyncMock(return_value=[_vec(1.0)])
    try:
        result = await semantic_search_sellers.ainvoke(
            {"user_id": str(user.id), "query": "anything", "limit": 10}
        )
    finally:
        emb_mod.embed_texts = orig
    assert [r["seller_id"] for r in result] == rows
