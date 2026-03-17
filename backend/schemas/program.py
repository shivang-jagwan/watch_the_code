from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProgramBase(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ProgramCreate(ProgramBase):
    pass


class ProgramUpdate(BaseModel):
    code: str | None = None
    name: str | None = None


class ProgramOut(ProgramBase):
    id: uuid.UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
