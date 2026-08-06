import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LeadScoreTier(str, enum.Enum):
    recommend = "recommend"
    watch = "watch"
    skip = "skip"


class LeadStatus(str, enum.Enum):
    new = "new"
    enriched = "enriched"
    scored = "scored"
    contacted = "contacted"


class Lead(Base):
    __tablename__ = "lead"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("campaign.id", ondelete="CASCADE"), index=True
    )
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storefront_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    market: Mapped[str] = mapped_column(String(32))
    profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # LLM 提取的结构化画像
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_tier: Mapped[LeadScoreTier | None] = mapped_column(
        Enum(LeadScoreTier, name="lead_score_tier"), nullable=True
    )
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status"), default=LeadStatus.new
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LeadSignal(Base):
    __tablename__ = "lead_signal"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("lead.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[str] = mapped_column(String(64))  # scale/category_match/shipping/reach/growth
    score: Mapped[float] = mapped_column(Float)
    evidence_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Contact(Base):
    __tablename__ = "contact"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("lead.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))  # email/phone/website
    value: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
