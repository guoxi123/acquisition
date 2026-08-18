"""query_db 语义排序单测：mock embed_texts + async_session，断言 SQL 走 cosine_distance
且保留排除已获取等 WHERE；embedding 失败时降级 LIKE。"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.v2 import nodes


def _make_state(**over):
    base = {
        "assistant_msg_id": None,
        "marketplace": "amazon.com",
        "category": "pet supplies",
        "target": 10,
        "user_id": str(uuid.uuid4()),
        "business_country": None,
        "min_total_feedback": None,
        "min_seller_score": None,
    }
    base.update(over)
    return base


def _capture_query(monkeypatch):
    """mock async_session，捕获 db.execute 收到的查询。"""
    captured = {}

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            captured["sql"] = str(q.compile(compile_kwargs={"literal_binds": True}))
            return FakeResult()

    import app.core.db as db_mod

    monkeypatch.setattr(db_mod, "async_session", lambda: FakeSession())
    # nodes.query_db 内部 from app.core.db import async_session（函数内 import，每次解析）
    monkeypatch.setattr("app.core.db.async_session", lambda: FakeSession())
    return captured


@pytest.mark.asyncio
async def test_semantic_order_by_cosine(monkeypatch):
    captured = _capture_query(monkeypatch)
    fake = [0.1] * 1024
    monkeypatch.setattr(
        "app.core.embedding.embed_texts", AsyncMock(return_value=[fake])
    )
    await nodes.query_db(_make_state())
    sql = captured["sql"]
    assert "<=>" in sql  # pgvector cosine_distance 编译为 <=> 操作符
    assert "embedding IS NOT NULL" in sql
    # 硬过滤保留：marketplace + 排除已获取
    assert "marketplace" in sql
    assert "user_acquired_sellers" in sql
    # 降级分支不应出现
    assert "LIKE" not in sql.upper()


@pytest.mark.asyncio
async def test_fallback_to_like_on_embedding_failure(monkeypatch):
    captured = _capture_query(monkeypatch)
    monkeypatch.setattr(
        "app.core.embedding.embed_texts",
        AsyncMock(side_effect=RuntimeError("api down")),
    )
    await nodes.query_db(_make_state())
    sql = captured["sql"]
    assert "like" in sql.lower()
    assert "lower(sellers.category)" in sql
    assert "total_feedback DESC" in sql
    assert "cosine_distance" not in sql
