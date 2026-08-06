"""配额服务 grant_sellers / get_quota_summary 验证脚本。

跑：cd backend && PYTHONPATH=. .venv/bin/python scripts/test_quota.py
覆盖：超额切分、永久去重、满额拦截、并发不超发、超管不限额、/me 摘要。
用临时用户 + 临时 sellers（QT_ 前缀），跑完即删。
"""
import asyncio
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import async_session, engine
from app.models.seller import Seller
from app.models.user import User, UserPlan
from app.models.user_acquired_seller import UserAcquiredSeller
from app.quota import grant_sellers, get_quota_summary


def _cands(prefix: str, n: int, base: int = 100) -> list[dict]:
    # seller_id 带 QT_ 前缀，便于跑完清理测试 sellers
    return [{"seller_id": f"QT_{prefix}{i}", "seller_score": base - i} for i in range(n)]


async def _seed_sellers(ids: list[str]) -> None:
    async with async_session() as db:
        for sid in ids:
            await db.execute(
                pg_insert(Seller)
                .values(seller_id=sid)
                .on_conflict_do_nothing(index_elements=[Seller.seller_id])
            )
        await db.commit()


async def _make_user(plan: UserPlan = UserPlan.free, super_admin: bool = False) -> uuid.UUID:
    async with async_session() as db:
        u = User(
            username=f"quota_test_{uuid.uuid4().hex[:8]}",
            password_hash="x",
            plan=plan,
            is_super_admin=super_admin,
        )
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
        return (
            await db.execute(
                select(func.count())
                .select_from(UserAcquiredSeller)
                .where(UserAcquiredSeller.user_id == uid)
            )
        ).scalar_one()


async def main() -> None:
    uid = await _make_user()
    try:
        # 场景1：free 额度 10，15 候选 → 发 10、跳 5、未满额
        c1 = _cands("S", 15)
        await _seed_sellers([c["seller_id"] for c in c1])
        async with async_session() as db:
            disp, m = await grant_sellers(db, uid, c1)
        assert len(disp) == 10, f"场景1 展示数 {len(disp)} != 10"
        assert m["new_granted"] == 10 and m["new_skipped"] == 5, m
        assert m["exhausted"] is False and m["upgrade_available"] is True, m
        assert await _acquired_count(uid) == 10
        print(f"[1] 超额切分 OK: 发{m['new_granted']} 跳{m['new_skipped']}")

        # 场景2：再 grant 同批 → 已发的永久去重；未发的（S10-S14）因额度满被挡
        async with async_session() as db:
            disp2, m2 = await grant_sellers(db, uid, c1)
        assert m2["new_granted"] == 0 and len(disp2) == 10, m2  # 历史仍展示
        assert m2["exhausted"] is True and m2["new_skipped"] == 5, m2  # S10-S14 被挡
        print(f"[2] 永久去重 OK: 新发{m2['new_granted']} 历史展示{len(disp2)} 被挡{m2['new_skipped']}")

        # 场景3：全新候选、额度已满 → 拦截，exhausted=True
        c3 = _cands("T", 15, base=90)
        await _seed_sellers([c["seller_id"] for c in c3])
        async with async_session() as db:
            disp3, m3 = await grant_sellers(db, uid, c3)
        assert m3["new_granted"] == 0 and len(disp3) == 0, m3
        assert m3["exhausted"] is True and m3["upgrade_available"] is True, m3
        print(f"[3] 满额拦截 OK: exhausted={m3['exhausted']}")

        # 场景4：并发 —— 新用户，gather 两次 grant 各 20 候选，落库总数应 = 10（不超发）
        uidc = await _make_user()
        try:
            c4 = _cands("C", 20)
            await _seed_sellers([c["seller_id"] for c in c4])

            async def _grant_once() -> int:
                async with async_session() as db:
                    _, mm = await grant_sellers(db, uidc, c4)
                    return mm["new_granted"]

            r1, r2 = await asyncio.gather(_grant_once(), _grant_once())
            total = await _acquired_count(uidc)
            assert total == 10, f"并发超发！落库 {total} != 10 (两次各发 {r1},{r2})"
            print(f"[4] 并发安全 OK: 两次各发 {r1}/{r2}, 落库 {total}")
        finally:
            await _cleanup_users(uidc)

        # 场景5：超管 unlimited，全发且不落库（grant 不写库，不触发 FK）
        uida = await _make_user(super_admin=True)
        try:
            async with async_session() as db:
                disp5, m5 = await grant_sellers(db, uida, _cands("A", 50))
            assert m5.get("unlimited") is True and len(disp5) == 50, m5
            assert await _acquired_count(uida) == 0
            print(f"[5] 超管不限额 OK: 全发 {len(disp5)}, 落库 0")
        finally:
            await _cleanup_users(uida)

        # 场景6：get_quota_summary（/me 用）
        async with async_session() as db:
            u = await db.get(User, uid)
            q = await get_quota_summary(db, u)
        assert q["plan"] == "free" and q["total"] == 10 and q["used"] == 10 and q["remaining"] == 0, q
        print(f"[6] /me 摘要 OK: {q}")

        print("\n✅ 全部场景通过")
    finally:
        await _cleanup_users(uid)
        await _cleanup_sellers()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
