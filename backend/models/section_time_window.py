from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class SectionTimeWindow(Base):
    __tablename__ = "section_time_windows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    section_id = Column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_slot_index = Column(Integer, nullable=False)
    end_slot_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("day_of_week >= 0 and day_of_week <= 5", name="ck_section_windows_day"),
        CheckConstraint("start_slot_index >= 0", name="ck_section_windows_start"),
        CheckConstraint("end_slot_index >= 0", name="ck_section_windows_end"),
        CheckConstraint("end_slot_index >= start_slot_index", name="ck_section_windows_order"),
    )
