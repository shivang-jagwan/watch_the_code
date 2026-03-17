from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class SubjectAllowedRoom(Base):
    __tablename__ = "subject_allowed_rooms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    room_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rooms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_exclusive = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_id", "room_id",
            name="uq_subject_allowed_rooms_tenant_subject_room",
        ),
    )
