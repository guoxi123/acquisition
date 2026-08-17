"""配额服务集成测试：grant_sellers / get_quota_summary。

需要真实 PG（连开发库），用临时用户 + QT_ 前缀 sellers，跑完清理。
迁移自 scripts/test_quota.py。

跑：cd backend && .venv/bin/python -m pytest tests/test_quota.py -v
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import db as db_mod
from app.models.seller import Seller
from app.models.user import User, UserPlan
from app.models.user_acquired_seller import UserAcquiredSeller
from app.quota import grant_sellers, get_quota_summary

# async_session 从 db_mod 动态取（conftest 替换 engine 后指向测试 loop 的 sessionmaker）
async_session = lambda: db_mod.async_session()

pytestmark = pytest.mark.asyncio


def _cands(prefix: str, n: int, base: int = 100) -> list[dict]:
    return [{"seller_id": f"QT_{prefix}{i}", "seller_score": base - i} for i in range(n)]


async def _seed_sellers(ids: list[str]) -> None:
    async with async_session() as db:
        for sid in ids:
            await db.execute(
                pg_insert(Seller).values(seller_id=sid).on_conflict_do_nothing(index_elements=[Seller.seller_id])
            )
        await db.commit()


async def _make_user(plan=UserPlan.free, super_admin=False) -> uuid.UUID:
    async with async_session() as db:
        u = User(username=f"qt_{uuid.uuid4().hex[:8]}", password_hash="x", plan=plan, is_super_admin=super_admin)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


async def _cleanup_users(*uids: uuid.UUID) -> None:
    async with async_session() as db:
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.commit()


async def _cleanup_sellers() -> None:
    async with async_session() as db:
        await db.execute(delete(Seller).where(Seller.seller_id.like("QT_%")))
        await db.commit()


async def _acquired_count(uid: uuid.UUID) -> int:
    async with async_session() as db:
        return (await db.execute(
            select(func.count()).select_from(UserAcquiredSeller).where(UserAcquiredSeller.user_id == uid)
        )).scalar_one()


@pytest.fixture
async def free_user():
    """临时 free 用户，测试后自动清理。"""
    uid = await _make_user()
    yield uid
    await _cleanup_users(uid)


@pytest.fixture
async def seeded_sellers():
    """seed QT_ 前缀 sellers，测试后清理。"""
    yield
    await _cleanup_sellers()


async def test_grant_free_quota_split(free_user, seeded_sellers):
    """free 额度 10，15 候选 → 发 10、跳 5"""
    uid = free_user
    c = _cands("S", 15)
    await _seed_sellers([x["seller_id"] for x in c])
    async with async_session() as db:
        disp, m = await grant_sellers(db, uid, c)
    assert len(disp) == 10
    assert m["new_granted"] == 10 and m["new_skipped"] == 5
    assert m["exhausted"] is False
    assert m["upgrade_available"] is True
    assert await _acquired_count(uid) == 10


async def test_grant_dedup_already_acquired(free_user, seeded_sellers):
    """再 grant 同批 → 已发的永久去重"""
    uid = free_user
    c = _cands("S", 15)
    await _seed_sellers([x["seller_id"] for x in c])
    async with async_session() as db:
        await grant_sellers(db, uid, c)  # 第一次发 10
    async with async_session() as db:
        disp2, m2 = await grant_sellers(db, uid, c)  # 第二次
    assert m2["new_granted"] == 0
    assert len(disp2) == 10  # 历史仍展示
    assert m2["exhausted"] is True


async def test_grant_full_quota_block(free_user, seeded_sellers):
    """满额后全新候选 → 拦截"""
    uid = free_user
    c1 = _cands("S", 10)
    await _seed_sellers([x["seller_id"] for x in c1])
    async with async_session() as db:
        await grant_sellers(db, uid, c1)  # 灌满
    c3 = _cands("T", 5, base=90)
    await _seed_sellers([x["seller_id"] for x in c3])
    async with async_session() as db:
        disp3, m3 = await grant_sellers(db, uid, c3)
    assert m3["new_granted"] == 0 and len(disp3) == 0
    assert m3["exhausted"] is True


async def test_grant_concurrent_no_oversell(seeded_sellers):
    """并发两次 grant 各 20 候选 → 落库总数 = 10（行锁防超发）"""
    uid = await _make_user()
    try:
        c = _cands("C", 20)
        await _seed_sellers([x["seller_id"] for x in c])

        async def _grant_once():
            async with async_session() as db:
                _, mm = await grant_sellers(db, uid, c)
                return mm["new_granted"]

        r1, r2 = await asyncio.gather(_grant_once(), _grant_once())
        total = await _acquired_count(uid)
        assert total == 10, f"并发超发！落库 {total} != 10 (两次各发 {r1},{r2})"
    finally:
        await _cleanup_users(uid)


async def test_grant_super_admin_unlimited(seeded_sellers):
    """超管：全发，落库（不限额度但记账）"""
    uida = await _make_user(super_admin=True)
    try:
        c = _cands("A", 50)
        await _seed_sellers([x["seller_id"] for x in c])
        async with async_session() as db:
            disp, m = await grant_sellers(db, uida, c)
        assert m.get("unlimited") is True
        assert len(disp) == 50
        assert await _acquired_count(uida) == 50  # 超管也落库
    finally:
        await _cleanup_users(uida)


async def test_quota_summary(free_user, seeded_sellers):
    """get_quota_summary 返回正确 plan/total/used/remaining"""
    uid = free_user
    c = _cands("S", 10)
    await _seed_sellers([x["seller_id"] for x in c])
    async with async_session() as db:
        await grant_sellers(db, uid, c)
        u = await db.get(User, uid)
        q = await get_quota_summary(db, u)
    assert q["plan"] == "free" and q["total"] == 10 and q["used"] == 10 and q["remaining"] == 0
