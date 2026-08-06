"""agent 主流程 harness 验证：query_db 去重/品类模糊 + acquire_if_needed 循环/早停/够则跳过 + check_quota 超配额。

跑：cd backend && PYTHONPATH=. .venv/bin/python scripts/test_agent_flow.py
"""
import asyncio
import uuid

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agent.v2 import nodes
from app.core.db import async_session, engine
from app.models.seller import Seller
from app.models.user import User, UserPlan
from app.models.user_acquired_seller import UserAcquiredSeller


async def _seed_sellers(ids, marketplace="amazon.com", category="outdoor furniture"):
    async with async_session() as db:
        for sid, fb in ids:
            await db.execute(
                pg_insert(Seller)
                .values(seller_id=sid, marketplace=marketplace, category=category, total_feedback=fb)
                .on_conflict_do_nothing(index_elements=[Seller.seller_id])
            )
        await db.commit()


async def _make_user(plan=UserPlan.free) -> uuid.UUID:
    async with async_session() as db:
        u = User(username=f"agent_test_{uuid.uuid4().hex[:8]}", password_hash="x", plan=plan)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _acquire(uid, sids):
    async with async_session() as db:
        for sid in sids:
            await db.execute(
                pg_insert(UserAcquiredSeller)
                .values(user_id=uid, seller_id=sid)
                .on_conflict_do_nothing(index_elements=[UserAcquiredSeller.user_id, UserAcquiredSeller.seller_id])
            )
        await db.commit()


async def _cleanup(uid):
    async with async_session() as db:
        await db.execute(delete(User).where(User.id == uid))
        await db.execute(delete(Seller).where(Seller.seller_id.like("AGT_%")))
        await db.commit()


async def main():
    uid = await _make_user()
    try:
        # === A. query_db 去重 + 品类模糊 ===
        # seed 10 个（品类用混合大小写 "Outdoor Furniture" 测归一化/ILIKE）
        await _seed_sellers([(f"AGT_S{i}", 1000 - i) for i in range(10)], category="Outdoor Furniture")
        await _acquire(uid, ["AGT_S0", "AGT_S1"])  # 用户已获取 S0,S1
        res = await nodes.query_db({
            "marketplace": "amazon.com", "category": "outdoor furniture",
            "remaining": 10, "user_id": str(uid), "assistant_msg_id": None,
        })
        ids = [s["seller_id"] for s in res["sellers"]]
        assert "AGT_S0" not in ids and "AGT_S1" not in ids, f"去重失败: {ids}"
        assert set(ids) == {f"AGT_S{i}" for i in range(2, 10)}, f"应返 S2-S9: {ids}"
        print(f"[A] query_db 去重+品类模糊 OK: 返 {len(ids)} 个（S0/S1 已去重，大小写归一命中）")

        # === B. acquire_if_needed 判断节点 + _route_acquire 路由 ===
        from app.agent.v2.graph import _route_acquire

        # 够 → llm_analysis
        assert _route_acquire({"sellers": [1, 2, 3], "remaining": 3, "fetch_round": 0}) == "llm_analysis"
        # 不够 → call_actors 子 agent
        assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 0}) == "call_actors"
        # 达 max_rounds → llm_analysis
        assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 3, "max_rounds": 3}) == "llm_analysis"
        # 上一轮 0 新增（源耗尽）→ llm_analysis
        assert _route_acquire({"sellers": [1], "remaining": 3, "fetch_round": 1, "last_new_count": 0}) == "llm_analysis"
        # 首轮 last_new=0 不算耗尽（还没采集过）
        assert _route_acquire({"sellers": [], "remaining": 3, "fetch_round": 0, "last_new_count": 0}) == "call_actors"
        # acquire_if_needed 是判断节点：只写 progress，不改 state
        r = await nodes.acquire_if_needed({"remaining": 3, "sellers": [1, 2], "fetch_round": 0, "assistant_msg_id": None})
        assert r == {}, r
        print(f"[B] acquire_if_needed 判断 + _route_acquire 路由 OK（够/上限/0新增→分析，不够→采集）")

        # === C. check_quota 超配额 ===
        # free 额度 10，灌满（S0-S9 共10，含已获取的 S0,S1）
        await _acquire(uid, [f"AGT_S{i}" for i in range(2, 10)])  # 补 S2-S9，共 10
        r = await nodes.check_quota({"user_id": str(uid), "assistant_msg_id": None})
        assert r["quota_exhausted"] is True and r["remaining"] == 0, r
        print(f"[C] check_quota 超配额 OK: exhausted={r['quota_exhausted']} remaining={r['remaining']}")

        print("\n✅ 全部通过")
    finally:
        await _cleanup(uid)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
