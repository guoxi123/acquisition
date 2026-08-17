"""auth API 集成测试：注册/登录/me 全流程 + 手机号格式 + 验证码校验 + 归属。

需要真实 PG（mock SMS），用临时手机号（13900000xxx），跑完清理。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.auth.security import create_access_token
from app.core import db as db_mod
from app.models.user import User

pytestmark = pytest.mark.asyncio

TEST_PHONE = "13900000888"
TEST_PASSWORD = "test123"


async def _cleanup():
    async with db_mod.async_session() as db:
        from sqlalchemy import select, delete as sa_delete
        from app.memory.models import MemorySession
        # 找到测试用户 ID
        uids = [r[0] for r in (await db.execute(select(User.id).where(User.phone == TEST_PHONE))).all()]
        if uids:
            await db.execute(sa_delete(MemorySession).where(MemorySession.user_id.in_(uids)))
            await db.execute(delete(User).where(User.id.in_(uids)))
        await db.commit()


@pytest.fixture
async def clean_db():
    """每个测试前后清理测试用户（避免重复注册冲突）"""
    await _cleanup()
    yield
    await _cleanup()


async def _client():
    """构建直接调 ASGI 的 AsyncClient（不走 HTTP 网络）。"""
    from app.main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_register_login_me_flow(clean_db):
    """注册（mock SMS）→ 登录 → /me 全流程"""
    async with await _client() as c:
        # 1. 发码（mock 模式，返回固定码）
        res = await c.post("/api/auth/sms/send", json={"phone": TEST_PHONE})
        assert res.status_code == 200, res.text

        # 取 DB 里的 code
        from app.models.sms_code import SmsCode
        async with db_mod.async_session() as db:
            from sqlalchemy import select
            rec = (await db.execute(
                select(SmsCode).where(SmsCode.phone == TEST_PHONE)
                .order_by(SmsCode.created_at.desc()).limit(1)
            )).scalars().first()
            code = rec.code

        # 2. 注册
        res = await c.post("/api/auth/register", json={
            "phone": TEST_PHONE, "code": code, "password": TEST_PASSWORD,
        })
        assert res.status_code == 200, res.text
        token = res.json()["token"]
        assert res.json()["user"]["phone"] == TEST_PHONE

        # 3. 手机号登录
        res = await c.post("/api/auth/login", json={
            "account": TEST_PHONE, "password": TEST_PASSWORD,
        })
        assert res.status_code == 200
        assert res.json()["user"]["phone"] == TEST_PHONE

        # 4. /me
        res = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["phone"] == TEST_PHONE
        assert "quota" in res.json()


async def test_register_duplicate_phone(clean_db):
    """重复注册 → 409（直接插入用户绕过发码防刷，再注册）"""
    from app.auth.security import hash_password
    async with db_mod.async_session() as db:
        from app.models.user import User as UserModel
        u = UserModel(username=TEST_PHONE, phone=TEST_PHONE, password_hash=hash_password(TEST_PASSWORD))
        db.add(u)
        await db.commit()

    async with await _client() as c:
        # 直接插了用户，模拟注册已存在场景 → 再注册应 409
        # 用手动插的验证码绕过防刷
        from app.models.sms_code import SmsCode
        from datetime import datetime, timedelta, timezone
        async with db_mod.async_session() as db:
            db.add(SmsCode(phone=TEST_PHONE, code="888888",
                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
            await db.commit()
        res = await c.post("/api/auth/register", json={
            "phone": TEST_PHONE, "code": "888888", "password": "other",
        })
        assert res.status_code == 409


async def test_register_wrong_code(clean_db):
    """错误验证码 → 400"""
    async with await _client() as c:
        await c.post("/api/auth/sms/send", json={"phone": TEST_PHONE})
        res = await c.post("/api/auth/register", json={
            "phone": TEST_PHONE, "code": "000000", "password": TEST_PASSWORD,
        })
        assert res.status_code == 400


async def test_login_wrong_password(clean_db):
    """密码错误 → 401"""
    async with await _client() as c:
        from app.models.sms_code import SmsCode
        await c.post("/api/auth/sms/send", json={"phone": TEST_PHONE})
        from sqlalchemy import select
        async with db_mod.async_session() as db:
            rec = (await db.execute(
                select(SmsCode).where(SmsCode.phone == TEST_PHONE)
                .order_by(SmsCode.created_at.desc()).limit(1)
            )).scalars().first()
            code = rec.code
        await c.post("/api/auth/register", json={
            "phone": TEST_PHONE, "code": code, "password": TEST_PASSWORD,
        })
        res = await c.post("/api/auth/login", json={
            "account": TEST_PHONE, "password": "wrongpassword",
        })
        assert res.status_code == 401


async def test_sms_send_invalid_phone():
    """手机号格式错 → 400"""
    async with await _client() as c:
        res = await c.post("/api/auth/sms/send", json={"phone": "123"})
        assert res.status_code == 400
