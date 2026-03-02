from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.sql import func

from models.base import Base


ROOM_TYPE = ENUM(
    "CLASSROOM",
    "LT",
    "LAB",
    name="room_type",
    create_type=False,
)


class Room(Base):
    __tablename__ = "rooms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    room_type = Column(ROOM_TYPE, nullable=False)
    capacity = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    is_special = Column(Boolean, nullable=False, default=False)
    special_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("capacity >= 0", name="ck_rooms_capacity"),
        UniqueConstraint("tenant_id", "code", name="uq_rooms_tenant_code"),
    )
