"""会话存储：会话/消息 CRUD + 序列号自增 + session 统计更新。

原始消息(memory_messages)只追加不改，压缩只更新 is_compressed 标记。
"""

import uuid

from sqlalchemy import func, select, update

from app.core.db import async_session
from app.memory.models import (
    MemoryMessage,
    MemorySession,
    MessageRole,
    SessionStatus,
)
from app.utils.logger import logger


async def create_session(
    context_data: dict | None = None, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    async with async_session() as db:
        s = MemorySession(
            status=SessionStatus.active, context_data=context_data, user_id=user_id
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.session_id


async def add_message(
    session_id: uuid.UUID,
    role: MessageRole,
    content: str,
    token_count: int | None = None,
    meta: dict | None = None,
) -> uuid.UUID:
    """追加消息，返回 message_id（UUID）+ 更新 session 统计。"""
    async with async_session() as db:
        max_seq = (
            await db.execute(
                select(func.max(MemoryMessage.sequence_number)).where(
                    MemoryMessage.session_id == session_id
                )
            )
        ).scalar() or 0
        seq = max_seq + 1
        message_id = uuid.uuid4()
        db.add(
            MemoryMessage(
                message_id=message_id,
                session_id=session_id,
                sequence_number=seq,
                role=role,
                content=content,
                token_count=token_count,
                meta=meta,
            )
        )
        await db.execute(
            update(MemorySession)
            .where(MemorySession.session_id == session_id)
            .values(
                total_messages=MemorySession.total_messages + 1,
                total_tokens=MemorySession.total_tokens + (token_count or 0),
            )
        )
        await db.commit()
        return message_id


async def get_messages(
    session_id: uuid.UUID, include_compressed: bool = True
) -> list[MemoryMessage]:
    async with async_session() as db:
        q = (
            select(MemoryMessage)
            .where(MemoryMessage.session_id == session_id)
            .order_by(MemoryMessage.sequence_number)
        )
        if not include_compressed:
            q = q.where(MemoryMessage.is_compressed == False)  # noqa: E712
        return list((await db.execute(q)).scalars().all())


async def get_uncompressed_messages(
    session_id: uuid.UUID, exclude_last_n: int = 4
) -> list[MemoryMessage]:
    """未压缩消息（排除最近 N 条保留），供压缩用。"""
    async with async_session() as db:
        msgs = list(
            (
                await db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.session_id == session_id,
                        MemoryMessage.is_compressed == False,  # noqa: E712
                    )
                    .order_by(MemoryMessage.sequence_number)
                )
            ).scalars().all()
        )
    return msgs[:-exclude_last_n] if exclude_last_n > 0 else msgs
