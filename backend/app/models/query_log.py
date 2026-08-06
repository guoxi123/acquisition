from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class QueryLog(Base):
    """查询日志：记录用户自然语言查询 + 解析结果 + 缓存命中/费用。"""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_marketplace: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parsed_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actor_cost: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
