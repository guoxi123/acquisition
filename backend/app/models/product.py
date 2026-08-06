from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Product(Base):
    """亚马逊产品（数据源 junglee/amazon-crawler）。asin 为主键，关联 seller。"""

    __tablename__ = "products"

    asin: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    price_value: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(3), server_default="USD")
    stars: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answered_questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bread_crumbs: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    seller_id: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("sellers.seller_id"), nullable=True, index=True
    )
    marketplace: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
