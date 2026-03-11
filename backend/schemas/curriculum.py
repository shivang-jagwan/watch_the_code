from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TrackSubjectBase(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    track: str = Field(min_length=1)
    subject_code: str = Field(min_length=1)
    is_elective: bool = False
    sessions_override: int | None = Field(default=None, ge=0)


class TrackSubjectCreate(TrackSubjectBase):
    pass


class TrackSubjectUpdate(BaseModel):
    track: str | None = None
    is_elective: bool | None = None
    sessions_override: int | None = Field(default=None, ge=0)


class TrackSubjectOut(BaseModel):
    id: uuid.UUID
    program_id: uuid.UUID
    academic_year_id: uuid.UUID
    track: str
    subject_id: uuid.UUID
    is_elective: bool
    sessions_override: int | None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Curriculum Subjects ────────────────────────────────────────────────────────


class CurriculumSubjectBase(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    track: str = "CORE"
    subject_code: str = Field(min_length=1)
    sessions_per_week: int = Field(default=0, ge=0)
    max_per_day: int = Field(default=1, ge=0)
    lab_block_size_slots: int = Field(default=1, ge=1)
    is_elective: bool = False


class CurriculumSubjectCreate(CurriculumSubjectBase):
    pass


class CurriculumSubjectUpdate(BaseModel):
    sessions_per_week: int | None = Field(default=None, ge=0)
    max_per_day: int | None = Field(default=None, ge=0)
    lab_block_size_slots: int | None = Field(default=None, ge=1)
    is_elective: bool | None = None
    track: str | None = None


class CurriculumSubjectOut(BaseModel):
    id: uuid.UUID
    program_id: uuid.UUID
    academic_year_id: uuid.UUID
    track: str
    subject_id: uuid.UUID
    sessions_per_week: int
    max_per_day: int
    lab_block_size_slots: int
    is_elective: bool
    created_at: datetime

    class Config:
        from_attributes = True
