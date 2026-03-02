from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.sql import func

from models.base import Base


RUN_STATUS = ENUM(
    "CREATED",
    "VALIDATION_FAILED",
    "INFEASIBLE",
    "FEASIBLE",
    "SUBOPTIMAL",
    "OPTIMAL",
    "ERROR",
    name="run_status",
    create_type=False,
)


class TimetableRun(Base):
    __tablename__ = "timetable_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    # Program-wide runs may span multiple academic years; year identity is stored
    # per TimetableEntry. Keep this nullable for program-global solves.
    academic_year_id = Column(UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status = Column(RUN_STATUS, nullable=False, default="CREATED")
    seed = Column(Integer, nullable=True)
    solver_version = Column(Text, nullable=True)
    parameters = Column(JSONB, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
