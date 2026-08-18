from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Seller(Base):
    """亚马逊卖家（数据源 automation-lab/amazon-sellers-scraper）。seller_id 为主键。"""

    __tablename__ = "sellers"

    seller_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)  # 采集时的品类
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    business_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_country: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )  # 从 address 解析（识别中国卖家）
    positive_rating_percent: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    total_feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recent_feedback_12mo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recent_feedback_90d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recent_feedback_30d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_since: Mapped[str | None] = mapped_column(String(50), nullable=True)
    response_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    marketplace: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    seller_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contacts: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )  # [{source, type, value}] 多来源（天眼查/企查查/官网/amazon）
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # RAG 语义检索向量（name/category/business_name 的 bge-m3 embedding）
    embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)
