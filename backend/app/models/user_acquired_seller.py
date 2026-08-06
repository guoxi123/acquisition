import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserAcquiredSeller(Base):
    """每用户已获取的卖家：永久去重的真相表 + 月度配额计数来源。

    (user_id, seller_id) 唯一约束兜底永久去重；acquired_at 带索引用于按月统计配额。
    """

    __tablename__ = "user_acquired_sellers"
    __table_args__ = (
        UniqueConstraint("user_id", "seller_id", name="uq_user_seller"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    seller_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("sellers.seller_id", ondelete="CASCADE"), index=True
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
