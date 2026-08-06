import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    done = "done"
    failed = "failed"


class Campaign(Base):
    __tablename__ = "campaign"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(255))  # 目标品类
    market: Mapped[str] = mapped_column(String(32))  # 目标市场，如 US
    constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 可选约束
    seller_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 可选：手动指定 sellerId
    max_sellers: Mapped[int] = mapped_column(default=5, server_default=text("5"))  # 自动发现评分上限
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"), default=CampaignStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
