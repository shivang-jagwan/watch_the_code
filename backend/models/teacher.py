from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False)

    weekly_off_day = Column(Integer, nullable=True)
    max_per_day = Column(Integer, nullable=False, default=4)
    max_per_week = Column(Integer, nullable=False, default=20)
    max_continuous = Column(Integer, nullable=False, default=3)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "weekly_off_day is null or (weekly_off_day >= 0 and weekly_off_day <= 5)",
            name="ck_teachers_weekly_off_day_range",
        ),
        UniqueConstraint("tenant_id", "code", name="uq_teachers_tenant_code"),
        CheckConstraint("max_per_day >= 0", name="ck_teachers_max_per_day"),
        CheckConstraint("max_per_week >= 0", name="ck_teachers_max_per_week"),
        CheckConstraint("max_continuous >= 1", name="ck_teachers_max_continuous"),
    )
