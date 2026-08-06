"""卖家获取配额服务：等级 → 月度额度 → 按剩余额度发放新卖家（永久去重 + 行锁防并发超发）。

设计要点：
- user_acquired_sellers 表是永久去重的真相源 + 月度配额计数来源。
- grant_sellers 用 SELECT ... FOR UPDATE 锁 user 行，串行化同一用户的发放，
  防止「两事务各读到 used=N 再各发」导致的按月超发；ON CONFLICT DO NOTHING 作纵深防御。
- 月度按 UTC：acquired_at 是 timestamptz；UTC 月初 = 北京每月 1 号 08:00。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserPlan
from app.models.user_acquired_seller import UserAcquiredSeller

# 各等级每月可获取的卖家数。扩展等级时：迁移加 enum 值 + 这里加一项。
PLAN_MONTHLY_QUOTA: dict[UserPlan, int] = {
    UserPlan.free: 10,
    UserPlan.basic: 200,
}


def effective_plan(user: User) -> UserPlan:
    """basic 且未过期 → basic；否则降级为 free。不持久化（只读判定）。"""
    if user.plan == UserPlan.basic and (
        user.plan_expires_at is None
        or user.plan_expires_at > datetime.now(timezone.utc)
    ):
        return UserPlan.basic
    return UserPlan.free


def _utc_month_start() -> datetime:
    """本月起始（UTC）。配额按自然月重置；若需按北京月，改用 ZoneInfo("Asia/Shanghai")。"""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def grant_sellers(
    db: AsyncSession, user_id: uuid.UUID, candidates: list[dict]
) -> tuple[list[str], dict]:
    """按剩余月度额度发放候选卖家（已按 seller_score 降序），返回 (授权展示的 seller_id 列表, 配额 meta)。

    - 超管 / 用户不存在（兼容降级）：全发不记账。
    - 永久去重：已在 user_acquired_sellers 的 seller 不再计入额度，但仍纳入展示集合。
    - 超额：新卖家一个都不发（拦截），meta.exhausted=True；本次有卖家被挡则 upgrade_available=True。
    """
    # 1) 锁 user 行 —— 串行化同一用户的发放，防并发超发
    user = (
        await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
    ).scalars().first()

    cand_ids = [c["seller_id"] for c in candidates if c.get("seller_id")]

    if user is None or user.is_super_admin:
        return cand_ids, {"unlimited": True}

    plan = effective_plan(user)
    quota = PLAN_MONTHLY_QUOTA[plan]

    # 2) 本月已用（锁内）
    month_start = _utc_month_start()
    used = (
        await db.execute(
            select(func.count())
            .select_from(UserAcquiredSeller)
            .where(
                UserAcquiredSeller.user_id == user_id,
                UserAcquiredSeller.acquired_at >= month_start,
            )
        )
    ).scalar_one()
    remaining = max(0, quota - used)

    # 3) 永久去重集合（不限本月）
    already = set(
        (
            await db.execute(
                select(UserAcquiredSeller.seller_id).where(
                    UserAcquiredSeller.user_id == user_id,
                    UserAcquiredSeller.seller_id.in_(cand_ids),
                )
            )
        ).scalars().all()
    )

    # 4) 按原评分序切配额
    to_grant: list[str] = []
    for sid in cand_ids:
        if sid in already:
            continue
        if len(to_grant) >= remaining:
            break
        to_grant.append(sid)
    new_total = len(cand_ids) - len(already)
    new_skipped = new_total - len(to_grant)

    # 5) 原子插入 + ON CONFLICT DO NOTHING（纵深防御）→ 提交释放行锁
    if to_grant:
        await db.execute(
            pg_insert(UserAcquiredSeller)
            .values([{"user_id": user_id, "seller_id": sid} for sid in to_grant])
            .on_conflict_do_nothing(
                index_elements=[UserAcquiredSeller.user_id, UserAcquiredSeller.seller_id]
            )
        )
    await db.commit()

    # 6) 展示集合 = 已获取(旧) + 本次新发，保持原评分序
    granted_set = already | set(to_grant)
    display = [sid for sid in cand_ids if sid in granted_set]
    return display, {
        "plan": plan.value,
        "quota": quota,
        "used": used,
        "remaining_after": max(0, remaining - len(to_grant)),
        "new_granted": len(to_grant),
        "new_skipped": new_skipped,
        "exhausted": remaining == 0 and new_total > 0,
        "upgrade_available": new_skipped > 0,
    }


async def get_quota_summary(db: AsyncSession, user: User) -> dict:
    """/me 用：当前等级、总额度、本月已用、剩余。超管 unlimited。"""
    if user.is_super_admin:
        return {"plan": "super", "total": None, "used": 0, "remaining": None, "unlimited": True}
    plan = effective_plan(user)
    total = PLAN_MONTHLY_QUOTA[plan]
    month_start = _utc_month_start()
    used = (
        await db.execute(
            select(func.count())
            .select_from(UserAcquiredSeller)
            .where(
                UserAcquiredSeller.user_id == user.id,
                UserAcquiredSeller.acquired_at >= month_start,
            )
        )
    ).scalar_one()
    return {
        "plan": plan.value,
        "total": total,
        "used": used,
        "remaining": max(0, total - used),
    }
