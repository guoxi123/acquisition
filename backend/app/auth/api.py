"""认证 API：POST /sms/send + /register + /login，GET /me。"""

import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.auth.deps import get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.db import async_session
from app.core.sms import send_sms_code
from app.models.sms_code import SmsCode
from app.models.user import User
from app.utils.logger import logger

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_SMS_TTL_MINUTES = 5
_RESEND_INTERVAL_SECONDS = 60


class SmsSendReq(BaseModel):
    phone: str


class RegisterReq(BaseModel):
    phone: str
    code: str
    password: str


class LoginReq(BaseModel):
    account: str  # 手机号或用户名（兼容超管）
    password: str


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "phone": u.phone,
        "is_super_admin": u.is_super_admin,
        "plan": u.plan.value if u.plan else "free",
        "plan_expires_at": u.plan_expires_at.isoformat() if u.plan_expires_at else None,
    }


@router.post("/sms/send")
async def sms_send(req: SmsSendReq) -> dict:
    """发送短信验证码：手机号格式校验 + 60s 防刷 + 生成码 + 存储 + 发送。"""
    phone = req.phone.strip()
    if not _PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        latest = (
            await db.execute(
                select(SmsCode)
                .where(SmsCode.phone == phone)
                .order_by(SmsCode.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if latest and latest.created_at:
            created = latest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() < _RESEND_INTERVAL_SECONDS:
                raise HTTPException(status_code=429, detail="验证码发送过于频繁，请 60 秒后再试")
        code = f"{secrets.randbelow(1000000):06d}"
        db.add(SmsCode(phone=phone, code=code, expires_at=now + timedelta(minutes=_SMS_TTL_MINUTES)))
        await db.commit()
    result = await send_sms_code(phone, code)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "短信发送失败"))
    return {"status": "sent", "mock": result.get("mock", False)}


@router.post("/register")
async def register(req: RegisterReq) -> dict:
    """注册：手机号 + 验证码 + 密码。校验码 → mark used → 建用户。"""
    phone = req.phone.strip()
    if not _PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    async with async_session() as db:
        record = (
            await db.execute(
                select(SmsCode)
                .where(
                    SmsCode.phone == phone,
                    SmsCode.code == req.code.strip(),
                    SmsCode.used.is_(False),
                    SmsCode.expires_at > datetime.now(timezone.utc),
                )
                .order_by(SmsCode.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if record is None:
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        if (await db.execute(select(User).where(User.phone == phone))).scalars().first():
            raise HTTPException(status_code=409, detail="该手机号已注册")
        # username 用手机号兜底（NOT NULL），phone 为正式登录标识
        user = User(username=phone, phone=phone, password_hash=hash_password(req.password))
        db.add(user)
        record.used = True
        await db.commit()
    logger.info(f"注册成功: phone={phone} (id={user.id})")
    return {"token": create_access_token({"sub": str(user.id)}), "user": _user_out(user)}


@router.post("/login")
async def login(req: LoginReq) -> dict:
    """登录：account（手机号或用户名）+ 密码。兼容超管 username 登录。"""
    account = req.account.strip()
    async with async_session() as db:
        user = (
            await db.execute(
                select(User).where(or_(User.phone == account, User.username == account))
            )
        ).scalars().first()
    if user is None or not verify_password(req.password, user.password_hash):
        logger.warning(f"登录失败: account={account}")
        raise HTTPException(status_code=401, detail="账号或密码错误")
    logger.info(f"登录成功: account={account} (super={user.is_super_admin})")
    return {"token": create_access_token({"sub": str(user.id)}), "user": _user_out(user)}


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    from app.quota import get_quota_summary

    async with async_session() as db:
        quota = await get_quota_summary(db, user)
    return {**_user_out(user), "quota": quota}
