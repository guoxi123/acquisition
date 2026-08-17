"""Agent 主流程集成测试：query_db 去重/品类模糊 + _route_acquire + check_quota。

需要真实 PG，用临时用户 + AGT_ 前缀 sellers，跑完清理。
迁移自 scripts/test_agent_flow.py。

跑：cd backend && .venv/bin/python -m pytest tests/test_agent_flow.py -v
"""
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agent.v2 import nodes
from app.core import db as db_mod
from app.models.seller import Seller
from app.models.user import User, UserPlan
from app.models.user_acquired_seller import UserAcquiredSeller

# async_session 从 db_mod 动态取（conftest 替换 engine 后指向测试 loop 的 sessionmaker）
async_session = lambda: db_mod.async_session()

pytestmark = pytest.mark.asyncio


async def _seed_sellers(ids, marketplace="amazon.com", category="outdoor furniture"):
    async with async_session() as db:
        for sid, fb in ids:
            await db.execute(
                pg_insert(Seller).values(seller_id=sid, marketplace=marketplace, category=category, total_feedback=fb)
                .on_conflict_do_nothing(index_elements=[Seller.seller_id])
            )
        await db.commit()


async def _make_user():
    async with async_session() as db:
        u = User(username=f"agt_{uuid.uuid4().hex[:8]}", password_hash="x", plan=UserPlan.free)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _acquire(uid, sids):
    async with async_session() as db:
        for sid in sids:
            await db.execute(
                pg_insert(UserAcquiredSeller).values(user_id=uid, seller_id=sid)
                .on_conflict_do_nothing(index_elements=[UserAcquiredSeller.user_id, UserAcquiredSeller.seller_id])
            )
        await db.commit()


@pytest.fixture
async def agent_user():
    uid = await _make_user()
    yield uid
    async with async_session() as db:
        await db.execute(delete(User).where(User.id == uid))
        await db.execute(delete(Seller).where(Seller.seller_id.like("AGT_%")))
        await db.commit()


async def test_query_db_dedup_and_category(agent_user):
    """query_db：去重已获取 + 品类模糊匹配（大小写归一）"""
    uid = agent_user
    await _seed_sellers([(f"AGT_S{i}", 1000 - i) for i in range(10)], category="Outdoor Furniture")
    await _acquire(uid, ["AGT_S0", "AGT_S1"])
    res = await nodes.query_db({
        "marketplace": "amazon.com", "category": "outdoor furniture",
        "remaining": 10, "user_id": str(uid), "assistant_msg_id": None,
    })
    ids = [s["seller_id"] for s in res["sellers"]]
    assert "AGT_S0" not in ids and "AGT_S1" not in ids
    assert set(ids) == {f"AGT_S{i}" for i in range(2, 10)}


async def test_acquire_if_needed_is_judgment_node():
    """acquire_if_needed 是判断节点：只写 progress，不改 state"""
    r = await nodes.acquire_if_needed({"remaining": 3, "sellers": [1, 2], "fetch_round": 0, "assistant_msg_id": None})
    assert r == {}


async def test_check_quota_exhausted(agent_user):
    """check_quota：灌满 10 → exhausted=True"""
    uid = agent_user
    await _seed_sellers([(f"AGT_S{i}", 1000) for i in range(10)])
    await _acquire(uid, [f"AGT_S{i}" for i in range(10)])
    r = await nodes.check_quota({"user_id": str(uid), "assistant_msg_id": None})
    assert r["quota_exhausted"] is True
    assert r["remaining"] == 0


async def test_check_quota_super_admin():
    """超管：remaining=50，不 exhausted"""
    async with async_session() as db:
        u = User(username=f"agt_sa_{uuid.uuid4().hex[:6]}", password_hash="x", is_super_admin=True)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        uid = u.id
    try:
        r = await nodes.check_quota({"user_id": str(uid), "assistant_msg_id": None})
        assert r["quota_exhausted"] is False
        assert r["remaining"] >= 50
    finally:
        async with async_session() as db:
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
