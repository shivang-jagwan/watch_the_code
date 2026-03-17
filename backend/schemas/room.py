from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RoomBase(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    room_type: str = Field(min_length=1)
    capacity: int = Field(default=0, ge=0)
    is_active: bool = True
    is_special: bool = False
    special_note: str | None = None


class RoomCreate(RoomBase):
    pass


class RoomUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    room_type: str | None = None
    capacity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    is_special: bool | None = None
    special_note: str | None = None


class RoomOut(RoomBase):
    id: uuid.UUID
    exclusive_subject_id: uuid.UUID | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
