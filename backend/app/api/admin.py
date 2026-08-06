"""超管 API：手动调整用户等级（plan）。初期不做支付，由超管开通。"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import get_super_admin
from app.core.db import async_session
from app.models.user import User, UserPlan
from app.utils.logger import logger

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_super_admin)])


class PlanUpdate(BaseModel):
    plan: UserPlan
    expires_at: datetime | None = None


@router.post("/users/{user_id}/plan")
async def update_user_plan(user_id: UUID, payload: PlanUpdate) -> dict:
    """开通/调整指定用户的等级与到期时间。仅超管。"""
    async with async_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            user = (
                await db.execute(select(User).where(User.username == str(user_id)))
            ).scalars().first()
        if user is None:
            logger.warning(f"[admin] 用户不存在: {user_id}")
            raise HTTPException(status_code=404, detail="用户不存在")
        user.plan = payload.plan
        user.plan_expires_at = payload.expires_at
        await db.commit()
        await db.refresh(user)
    logger.info(
        f"[admin] 更新用户等级: {user.username} -> {payload.plan.value} (至 {payload.expires_at})"
    )
    return {
        "id": str(user.id),
        "username": user.username,
        "plan": user.plan.value,
        "plan_expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
    }
