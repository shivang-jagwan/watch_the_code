from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class CurriculumSubject(Base):
    """Teaching requirement: how a subject is delivered within a program/year/track.

    Separates *what* the subject is (subjects table) from *how many times per
    week* it must be scheduled (this table).  Allows the same subject to have
    different session counts in different academic years or tracks.
    """

    __tablename__ = "curriculum_subjects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    program_id = Column(
        UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    academic_year_id = Column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'CORE' | 'CYBER' | 'AI_DS' | 'AI_ML' | future tracks
    track = Column(Text, nullable=False, default="CORE")
    subject_id = Column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sessions_per_week = Column(Integer, nullable=False, default=0)
    max_per_day = Column(Integer, nullable=False, default=1)
    lab_block_size_slots = Column(Integer, nullable=False, default=1)
    is_elective = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "program_id", "academic_year_id", "track", "subject_id",
            name="uq_curriculum_subjects",
        ),
        CheckConstraint("sessions_per_week >= 0", name="ck_curriculum_subjects_sessions"),
        CheckConstraint("max_per_day >= 0", name="ck_curriculum_subjects_max_per_day"),
        CheckConstraint("lab_block_size_slots >= 1", name="ck_curriculum_subjects_lab_block"),
    )
