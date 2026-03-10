from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.sql import func

from models.base import Base


SUBJECT_TYPE = ENUM(
    "THEORY",
    "LAB",
    name="subject_type",
    create_type=False,
)


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    academic_year_id = Column(UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False)
    code = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    subject_type = Column(SUBJECT_TYPE, nullable=False)
    sessions_per_week = Column(Integer, nullable=False)
    max_per_day = Column(Integer, nullable=False, default=1)
    lab_block_size_slots = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Academic credit value (0 = not set).
    credits = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("sessions_per_week >= 0", name="ck_subjects_sessions_per_week"),
        UniqueConstraint("tenant_id", "code", name="uq_subjects_tenant_code"),
        CheckConstraint("max_per_day >= 0", name="ck_subjects_max_per_day"),
        CheckConstraint("lab_block_size_slots >= 1", name="ck_subjects_lab_block_size"),
        CheckConstraint("credits >= 0", name="ck_subjects_credits"),
    )
