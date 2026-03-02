from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.sql import func

from models.base import Base


CONFLICT_SEVERITY = ENUM(
    "INFO",
    "WARN",
    "ERROR",
    name="conflict_severity",
    create_type=False,
)


class TimetableConflict(Base):
    __tablename__ = "timetable_conflicts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("timetable_runs.id", ondelete="CASCADE"), nullable=False)
    severity = Column(CONFLICT_SEVERITY, nullable=False, default="ERROR")
    conflict_type = Column(Text, nullable=False)
    message = Column(Text, nullable=False)

    section_id = Column(UUID(as_uuid=True), nullable=True)
    teacher_id = Column(UUID(as_uuid=True), nullable=True)
    subject_id = Column(UUID(as_uuid=True), nullable=True)
    room_id = Column(UUID(as_uuid=True), nullable=True)
    slot_id = Column(UUID(as_uuid=True), nullable=True)

    # New structured diagnostics payload (for drill-down in UI).
    details_json = Column("details", JSONB, nullable=True)

    # Legacy field kept for backwards-compatibility.
    metadata_json = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
