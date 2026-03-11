from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SectionBase(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    strength: int = Field(default=0, ge=0)
    track: str = Field(default='CORE', min_length=1)
    is_active: bool = True
    max_daily_slots: int | None = None
    pass


class SectionCreate(SectionBase):
    pass


class SectionUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    strength: int | None = Field(default=None, ge=0)
    track: str | None = None
    is_active: bool | None = None
    max_daily_slots: int | None = None


class SectionPut(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    strength: int = Field(default=0, ge=0)
    track: str = Field(min_length=1)
    is_active: bool = True
    max_daily_slots: int | None = None


class SectionStrengthPut(BaseModel):
    strength: int = Field(default=0, ge=0)


class SectionOut(BaseModel):
    id: uuid.UUID
    program_id: uuid.UUID
    academic_year_id: uuid.UUID
    code: str
    name: str
    strength: int
    track: str
    is_active: bool
    max_daily_slots: int | None
    created_at: datetime

    class Config:
        from_attributes = True
