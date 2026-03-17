from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TeacherBase(BaseModel):
    code: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    weekly_off_day: int | None = Field(default=None, ge=0, le=5)
    max_per_day: int = Field(default=4, ge=0)
    max_per_week: int = Field(default=20, ge=0)
    max_continuous: int = Field(default=3, ge=1)
    is_active: bool = True


class TeacherCreate(TeacherBase):
    pass


class TeacherUpdate(BaseModel):
    code: str | None = None
    full_name: str | None = None
    weekly_off_day: int | None = Field(default=None, ge=0, le=5)
    max_per_day: int | None = Field(default=None, ge=0)
    max_per_week: int | None = Field(default=None, ge=0)
    max_continuous: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class TeacherPut(BaseModel):
    full_name: str = Field(min_length=1)
    weekly_off_day: int | None = Field(default=None, ge=0, le=5)
    max_per_day: int = Field(default=4, ge=0, le=6)
    max_per_week: int = Field(default=20, ge=0, le=36)
    max_continuous: int = Field(default=3, ge=1)
    is_active: bool = True


class TeacherOut(TeacherBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
