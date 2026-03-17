from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TeacherTimeWindowItem(BaseModel):
    # None means the window applies to every active working day.
    day_of_week: int | None = Field(default=None, ge=0, le=5)
    start_slot_index: int = Field(ge=0)
    end_slot_index: int = Field(ge=0)
    is_strict: bool = True

    @model_validator(mode="after")
    def _check_order(self) -> "TeacherTimeWindowItem":
        if self.end_slot_index < self.start_slot_index:
            raise ValueError("end_slot_index must be >= start_slot_index")
        return self


class TeacherTimeWindowCreate(TeacherTimeWindowItem):
    pass


class TeacherTimeWindowOut(TeacherTimeWindowItem):
    id: uuid.UUID
    teacher_id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PutTeacherTimeWindowsRequest(BaseModel):
    windows: list[TeacherTimeWindowItem] = Field(default_factory=list)


class ListTeacherTimeWindowsResponse(BaseModel):
    teacher_id: uuid.UUID
    windows: list[TeacherTimeWindowOut]
