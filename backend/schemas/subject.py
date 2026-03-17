from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubjectBase(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    sessions_per_week: int = Field(default=0, ge=0)
    max_per_day: int = Field(default=1, ge=0)
    lab_block_size_slots: int = Field(default=1, ge=1)
    is_active: bool = True
    credits: int = Field(default=0, ge=0)


class SubjectCreate(SubjectBase):
    pass


class SubjectUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    subject_type: str | None = None
    sessions_per_week: int | None = Field(default=None, ge=0)
    max_per_day: int | None = Field(default=None, ge=0)
    lab_block_size_slots: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    credits: int | None = Field(default=None, ge=0)


class SubjectPut(BaseModel):
    name: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    sessions_per_week: int = Field(ge=1, le=6)
    max_per_day: int = Field(ge=1)
    lab_block_size_slots: int = Field(ge=1)
    is_active: bool = True
    credits: int = Field(default=0, ge=0)


class SubjectOut(BaseModel):
    id: uuid.UUID
    program_id: uuid.UUID
    academic_year_id: uuid.UUID
    code: str
    name: str
    subject_type: str
    sessions_per_week: int
    max_per_day: int
    lab_block_size_slots: int
    is_active: bool
    credits: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SubjectAllowedRoomOut(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    room_id: uuid.UUID
    is_exclusive: bool = False
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ListSubjectAllowedRoomsResponse(BaseModel):
    subject_id: uuid.UUID
    room_ids: list[uuid.UUID]
    exclusive_room_ids: list[uuid.UUID] = []
