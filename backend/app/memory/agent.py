"""ChatAgent：集成 storage + compressor，提供会话管理 + 三层上下文 + 可追溯。"""

import uuid

from sqlalchemy import select

from app.core.db import async_session
from app.memory import compressor, storage
from app.memory.models import (
    MemoryCompressionVersion,
    MemoryMessage,
    MemoryMessageCompressionMap,
    MessageRole,
)

PRESERVE_LAST_N = 4


async def create_session(
    context_data: dict | None = None, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    return await storage.create_session(context_data, user_id)


async def add_message_and_maybe_compress(
    session_id: uuid.UUID,
    role: MessageRole,
    content: str,
    token_count: int | None = None,
    meta: dict | None = None,
):
    """存消息 + 触发压缩检查。返回 (sequence_number, version_or_None)。"""
    seq = await storage.add_message(session_id, role, content, token_count, meta)
    version = await compressor.maybe_compress(session_id)
    return seq, version


async def get_context(session_id: uuid.UUID) -> dict:
    """三层上下文：压缩摘要（全部版本）+ 最近 N 条未压缩原文。"""
    async with async_session() as db:
        versions = list(
            (
                await db.execute(
                    select(MemoryCompressionVersion)
                    .where(MemoryCompressionVersion.session_id == session_id)
                    .order_by(MemoryCompressionVersion.version_number)
                )
            ).scalars().all()
        )
    # 全部未压缩消息都作为"近期原文"给 LLM（压缩保证未压缩数受控：触发阈值16→压到4+新增）
    recent = await storage.get_messages(session_id, include_compressed=False)
    return {
        "summaries": [
            {
                "version": v.version_number,
                "content": v.compressed_content,
                "range": [v.start_sequence_number, v.end_sequence_number],
                "ratio": v.compression_ratio,
            }
            for v in versions
        ],
        "recent_messages": [
            {"seq": m.sequence_number, "role": m.role.value, "content": m.content}
            for m in recent
        ],
    }


async def trace_message(message_id: uuid.UUID) -> dict:
    """反向可追溯：原始消息 → 被压缩到哪些版本（即使发给 LLM 的是摘要，也能取回原文）。"""
    async with async_session() as db:
        msg = await db.get(MemoryMessage, message_id)
        maps = list(
            (
                await db.execute(
                    select(MemoryMessageCompressionMap).where(
                        MemoryMessageCompressionMap.message_id == message_id
                    )
                )
            ).scalars().all()
        )
        versions = []
        for mp in maps:
            v = await db.get(MemoryCompressionVersion, mp.version_id)
            if v:
                versions.append(
                    {"version": v.version_number, "summary": v.compressed_content}
                )
    return {
        "message": (
            {"seq": msg.sequence_number, "role": msg.role.value, "content": msg.content}
            if msg
            else None
        ),
        "compressed_in": versions,
    }
