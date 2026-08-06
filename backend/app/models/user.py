import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserPlan(str, enum.Enum):
    """用户等级。扩展时：迁移里 ALTER TYPE user_plan ADD VALUE + PLAN_MONTHLY_QUOTA 加一项。"""

    free = "free"
    basic = "basic"


class User(Base):
    """用户。is_super_admin 超管不限卖家获取额度；普通用户按 plan 的月度配额发放。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[UserPlan] = mapped_column(
        Enum(UserPlan, name="user_plan"), default=UserPlan.free, server_default="free"
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
