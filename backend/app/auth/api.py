"""认证 API：POST /register + /login，GET /me。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.db import async_session
from app.models.user import User
from app.utils.logger import logger

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthReq(BaseModel):
    username: str
    password: str


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "is_super_admin": u.is_super_admin,
        "plan": u.plan.value if u.plan else "free",
        "plan_expires_at": u.plan_expires_at.isoformat() if u.plan_expires_at else None,
    }


@router.post("/register")
async def register(req: AuthReq) -> dict:
    logger.info(f"注册请求: username={req.username}")
    async with async_session() as db:
        if (
            await db.execute(select(User).where(User.username == req.username))
        ).scalars().first():
            logger.warning(f"注册失败，用户名已存在: {req.username}")
            raise HTTPException(status_code=409, detail="用户名已存在")
        user = User(username=req.username, password_hash=hash_password(req.password))
        db.add(user)
        await db.commit()
    logger.info(f"注册成功: {req.username} (id={user.id})")
    return {"token": create_access_token({"sub": str(user.id)}), "user": _user_out(user)}


@router.post("/login")
async def login(req: AuthReq) -> dict:
    logger.info(f"登录请求: username={req.username}")
    async with async_session() as db:
        user = (
            await db.execute(select(User).where(User.username == req.username))
        ).scalars().first()
    if user is None or not verify_password(req.password, user.password_hash):
        logger.warning(f"登录失败: {req.username} (用户不存在或密码错误)")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    logger.info(f"登录成功: {req.username} (super={user.is_super_admin})")
    return {"token": create_access_token({"sub": str(user.id)}), "user": _user_out(user)}


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    logger.debug(f"查询当前用户: {user.username}")
    from app.quota import get_quota_summary

    async with async_session() as db:
        quota = await get_quota_summary(db, user)
    return {**_user_out(user), "quota": quota}
