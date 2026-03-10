from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class TeacherTimeWindow(Base):
    __tablename__ = "teacher_time_windows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = Column(UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False)
    # NULL day_of_week means the window applies to every active working day.
    day_of_week = Column(Integer, nullable=True)
    start_slot_index = Column(Integer, nullable=False)
    end_slot_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 5)",
            name="ck_teacher_windows_day",
        ),
        CheckConstraint("start_slot_index >= 0", name="ck_teacher_windows_start"),
        CheckConstraint("end_slot_index >= 0", name="ck_teacher_windows_end"),
        CheckConstraint("end_slot_index >= start_slot_index", name="ck_teacher_windows_order"),
        UniqueConstraint("tenant_id", "teacher_id", "day_of_week", name="uq_teacher_windows_tenant_teacher_day"),
    )
