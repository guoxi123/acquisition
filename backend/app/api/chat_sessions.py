"""会话历史 API：GET /api/chat/sessions + /sessions/{id}/messages。

刷新页面后恢复用户的会话列表与消息历史。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth.deps import get_current_user
from app.core.db import async_session
from app.memory import agent as memory_agent
from app.memory.models import MemoryMessage, MemorySession, MessageRole
from app.memory.storage import get_messages
from app.models.user import User

router = APIRouter(prefix="/api/chat/sessions", tags=["chat"])


@router.post("")
async def create_session_api(user: User = Depends(get_current_user)) -> dict:
    """创建空会话（卖家获取受月度配额管控，会话数不限）。"""
    sid = await memory_agent.create_session(user_id=user.id)
    return {"session_id": str(sid)}


@router.get("")
async def list_sessions(user: User = Depends(get_current_user)) -> dict:
    """当前用户的会话列表（按创建时间倒序）。"""
    async with async_session() as db:
        sessions = list(
            (
                await db.execute(
                    select(MemorySession)
                    .where(MemorySession.user_id == user.id)
                    .order_by(MemorySession.created_at.desc())
                )
            ).scalars().all()
        )
        result = []
        for s in sessions:
            first_user_msg = (
                await db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.session_id == s.session_id,
                        MemoryMessage.role == MessageRole.user,
                    )
                    .order_by(MemoryMessage.sequence_number)
                    .limit(1)
                )
            ).scalars().first()
            result.append(
                {
                    "session_id": str(s.session_id),
                    "created_at": s.created_at.isoformat(),
                    "total_messages": s.total_messages,
                    "title": (first_user_msg.content[:40] if first_user_msg else "新会话"),
                }
            )
    return {"sessions": result}


@router.get("/{session_id}/messages")
async def get_messages_api(
    session_id: str, user: User = Depends(get_current_user)
) -> dict:
    """会话消息历史（验证归属：自己的或超管）。"""
    async with async_session() as db:
        session = await db.get(MemorySession, uuid.UUID(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.user_id != user.id and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    msgs = await get_messages(uuid.UUID(session_id))
    return {
        "messages": [
            {"seq": m.sequence_number, "role": m.role.value, "content": m.content, "meta": m.meta}
            for m in msgs
        ]
    }
