"""chat_sessions CRUD 集成测试：创建会话 → 查消息 → 重命名 → 删除 → 归属校验。

需要真实 PG，用临时用户，跑完清理。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.auth.security import create_access_token
from app.core import db as db_mod
from app.memory import agent as memory_agent
from app.memory.models import MemorySession
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _make_user_and_token(prefix="sess_test"):
    """创建临时用户 + token，返回 (uid, token)。"""
    async with db_mod.async_session() as db:
        u = User(username=f"{prefix}_{uuid.uuid4().hex[:6]}", password_hash="x", plan="free")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        uid = u.id
    token = create_access_token({"sub": str(uid)})
    return uid, token


async def _cleanup_user(uid):
    """清理用户：先删关联会话（FK 约束），再删用户。"""
    async with db_mod.async_session() as db:
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(MemorySession).where(MemorySession.user_id == uid))
        await db.execute(delete(User).where(User.id == uid))
        await db.commit()


async def _client():
    from app.main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def user_and_token():
    uid, token = await _make_user_and_token()
    yield uid, token
    await _cleanup_user(uid)


async def test_create_list_rename_delete_session(user_and_token):
    """创建 → 列表 → 重命名 → 删除"""
    uid, token = user_and_token
    headers = {"Authorization": f"Bearer {token}"}
    async with await _client() as c:
        # 1. 创建会话
        res = await c.post("/api/chat/sessions", headers=headers)
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # 2. 列表（含该会话）
        res = await c.get("/api/chat/sessions", headers=headers)
        assert res.status_code == 200
        sids = [s["session_id"] for s in res.json()["sessions"]]
        assert sid in sids

        # 3. 重命名
        res = await c.patch(f"/api/chat/sessions/{sid}", json={"title": "我的测试会话"}, headers=headers)
        assert res.status_code == 200
        assert res.json()["title"] == "我的测试会话"

        # 4. 列表确认 title 更新
        res = await c.get("/api/chat/sessions", headers=headers)
        titles = {s["session_id"]: s["title"] for s in res.json()["sessions"]}
        assert titles[sid] == "我的测试会话"

        # 5. 删除
        res = await c.delete(f"/api/chat/sessions/{sid}", headers=headers)
        assert res.status_code == 200

        # 6. 列表确认已删
        res = await c.get("/api/chat/sessions", headers=headers)
        sids = [s["session_id"] for s in res.json()["sessions"]]
        assert sid not in sids


async def test_get_messages_empty_session(user_and_token):
    """空会话 → 消息列表为空"""
    uid, token = user_and_token
    headers = {"Authorization": f"Bearer {token}"}
    async with await _client() as c:
        res = await c.post("/api/chat/sessions", headers=headers)
        sid = res.json()["session_id"]
        res = await c.get(f"/api/chat/sessions/{sid}/messages", headers=headers)
        assert res.status_code == 200
        assert res.json()["messages"] == []


async def test_rename_empty_title_rejected(user_and_token):
    """空标题 → 400"""
    uid, token = user_and_token
    headers = {"Authorization": f"Bearer {token}"}
    async with await _client() as c:
        res = await c.post("/api/chat/sessions", headers=headers)
        sid = res.json()["session_id"]
        res = await c.patch(f"/api/chat/sessions/{sid}", json={"title": "  "}, headers=headers)
        assert res.status_code == 400


async def test_delete_other_users_session_forbidden(user_and_token):
    """删别人的会话 → 403"""
    uid1, token1 = user_and_token
    uid2, token2 = await _make_user_and_token(prefix="other")
    try:
        headers1 = {"Authorization": f"Bearer {token1}"}
        headers2 = {"Authorization": f"Bearer {token2}"}
        async with await _client() as c:
            # 用户1建会话
            res = await c.post("/api/chat/sessions", headers=headers1)
            sid = res.json()["session_id"]
            # 用户2试图删 → 403
            res = await c.delete(f"/api/chat/sessions/{sid}", headers=headers2)
            assert res.status_code == 403
    finally:
        await _cleanup_user(uid2)


async def test_get_nonexistent_session_404(user_and_token):
    """不存在的会话 → 404"""
    _, token = user_and_token
    headers = {"Authorization": f"Bearer {token}"}
    fake_sid = str(uuid.uuid4())
    async with await _client() as c:
        res = await c.get(f"/api/chat/sessions/{fake_sid}/messages", headers=headers)
        assert res.status_code == 404
