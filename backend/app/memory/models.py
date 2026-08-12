"""会话压缩 Agent 系统数据模型（按 docs/MEMORY.md §4）。

4 表：memory_sessions / memory_messages / memory_compression_versions / message_compression_map。
表名加 memory_ 前缀，避免和现有 campaign/lead/sellers 等冲突。
原始消息(memory_messages)永不修改，压缩只产生衍生视图。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SessionStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    expired = "expired"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"
    function = "function"


class CompressionStrategy(str, enum.Enum):
    summary = "summary"
    key_points = "key_points"
    hierarchical = "hierarchical"
    token_window = "token_window"
    custom = "custom"


class CompressionVersionStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"
    failed = "failed"


class MessageRoleInCompression(str, enum.Enum):
    source = "source"
    reference = "reference"
    context = "context"


class MemorySession(Base):
    """会话主表：记录会话基本信息与状态。"""

    __tablename__ = "memory_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="memory_session_status"), default=SessionStatus.active
    )
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    context_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 自定义标题；为空时取首条用户消息
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryMessage(Base):
    """原始消息表（Source of Truth）：永不修改，只更新 is_compressed 标记。"""

    __tablename__ = "memory_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_memory_message_seq"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memory_sessions.session_id", ondelete="CASCADE"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="memory_message_role"))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_compressed: Mapped[bool] = mapped_column(default=False)
    compression_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("memory_compression_versions.version_id"), nullable=True
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 补充数据（如 sellers 表格）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MemoryCompressionVersion(Base):
    """压缩版本表：记录每次压缩结果，支持多版本与可追溯。"""

    __tablename__ = "memory_compression_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memory_sessions.session_id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[CompressionStrategy] = mapped_column(
        Enum(CompressionStrategy, name="memory_compression_strategy"),
        default=CompressionStrategy.summary,
    )
    start_sequence_number: Mapped[int] = mapped_column(Integer)
    end_sequence_number: Mapped[int] = mapped_column(Integer)
    compressed_content: Mapped[str] = mapped_column(Text)
    original_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compressed_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compression_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[CompressionVersionStatus] = mapped_column(
        Enum(CompressionVersionStatus, name="memory_compression_version_status"),
        default=CompressionVersionStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MemoryMessageCompressionMap(Base):
    """消息压缩映射表：原始消息 ↔ 压缩版本多对多，可追溯性核心。"""

    __tablename__ = "memory_message_compression_map"
    __table_args__ = (
        UniqueConstraint("message_id", "version_id", name="uq_memory_map_msg_version"),
    )

    map_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memory_messages.message_id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("memory_compression_versions.version_id", ondelete="CASCADE"),
        index=True,
    )
    message_role_in_compression: Mapped[MessageRoleInCompression] = mapped_column(
        Enum(MessageRoleInCompression, name="memory_message_role_in_compression"),
        default=MessageRoleInCompression.source
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
