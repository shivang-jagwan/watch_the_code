from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.sql import func

from models.base import Base


SECTION_TRACK = ENUM(
    "CORE",
    "CYBER",
    "AI_DS",
    "AI_ML",
    name="section_track",
    create_type=False,
)


class Section(Base):
    __tablename__ = "sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    academic_year_id = Column(UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False)
    code = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    strength = Column(Integer, nullable=False, default=0)
    track = Column(SECTION_TRACK, nullable=False, default="CORE")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("strength >= 0", name="ck_sections_strength"),
        UniqueConstraint("tenant_id", "code", name="uq_sections_tenant_code"),
    )
