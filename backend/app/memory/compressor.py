"""会话压缩：message_count 触发 + summary 策略（DeepSeek）+ 增量 + 映射 + 标记。

流程：检测未压缩数≥阈值 → 取未压缩（排除最近 N）→ DeepSeek summary →
建 compression_version → 建 message_compression_map → 标记 messages.is_compressed。
"""

from sqlalchemy import func, select, update

from app.core.db import async_session
from app.memory import storage
from app.memory.models import (
    CompressionStrategy,
    CompressionVersionStatus,
    MemoryCompressionVersion,
    MemoryMessage,
    MemoryMessageCompressionMap,
    MessageRoleInCompression,
)
from app.utils.logger import logger

# MVP 配置（后续可抽到 compression_configs 表）
TRIGGER_THRESHOLD = 16  # 未压缩消息数达此值触发
PRESERVE_LAST_N = 4  # 保留最近 N 条不压缩


async def check_compression_needed(session_id) -> bool:
    async with async_session() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(MemoryMessage)
                .where(
                    MemoryMessage.session_id == session_id,
                    MemoryMessage.is_compressed == False,  # noqa: E712
                )
            )
        ).scalar() or 0
    return count >= TRIGGER_THRESHOLD


async def compress(session_id) -> MemoryCompressionVersion | None:
    """增量压缩：未压缩消息（排除最近 N）→ summary → 版本 + 映射 + 标记。"""
    from app.agent.llm import get_llm

    msgs = await storage.get_uncompressed_messages(session_id, exclude_last_n=PRESERVE_LAST_N)
    if len(msgs) < PRESERVE_LAST_N:
        return None

    async with async_session() as db:
        max_vn = (
            await db.execute(
                select(func.max(MemoryCompressionVersion.version_number)).where(
                    MemoryCompressionVersion.session_id == session_id
                )
            )
        ).scalar() or 0
    version_number = max_vn + 1

    # summary（DeepSeek）
    dialog = "\n".join(f"{m.role.value}: {m.content[:200]}" for m in msgs)
    llm = get_llm()
    resp = await llm.ainvoke(
        f"把以下对话压缩成简洁摘要（保留：用户需求、已确认方案、关键数据/决策），限 300 字：\n{dialog}"
    )
    summary = str(resp.content)[:1200]

    original_tokens = sum((m.token_count or len(m.content.split())) for m in msgs)
    compressed_tokens = len(summary.split())
    ratio = round(compressed_tokens / original_tokens, 2) if original_tokens else None

    async with async_session() as db:
        version = MemoryCompressionVersion(
            session_id=session_id,
            version_number=version_number,
            strategy=CompressionStrategy.summary,
            start_sequence_number=msgs[0].sequence_number,
            end_sequence_number=msgs[-1].sequence_number,
            compressed_content=summary,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=ratio,
            status=CompressionVersionStatus.active,
        )
        db.add(version)
        await db.flush()  # 拿 version_id

        for m in msgs:
            db.add(
                MemoryMessageCompressionMap(
                    message_id=m.message_id,
                    version_id=version.version_id,
                    message_role_in_compression=MessageRoleInCompression.source,
                )
            )
            await db.execute(
                update(MemoryMessage)
                .where(MemoryMessage.message_id == m.message_id)
                .values(is_compressed=True, compression_version_id=version.version_id)
            )
        await db.commit()
        return version


async def maybe_compress(session_id):
    """检查阈值 + 压缩（chat 每条消息后调）。返回版本或 None。"""
    if await check_compression_needed(session_id):
        logger.info(f"[compressor] 触发压缩: session={session_id}")
        return await compress(session_id)
    return None
