import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ApifyCache(Base):
    """Apify 数据缓存：按 source + cache_key 存原始数据，TTL 过期。

    provider 先查此表（未过期直接返回），命中免调 Apify，省费用。
    cache_set 用 upsert（同 source+cache_key 覆盖）。
    """

    __tablename__ = "apify_cache"
    __table_args__ = (
        UniqueConstraint("source", "cache_key", name="uq_apify_cache_source_key"),
        Index("ix_apify_cache_lookup", "source", "cache_key", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(512))
    data: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
