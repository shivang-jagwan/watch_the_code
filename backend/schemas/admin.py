from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AcademicYearOut(BaseModel):
    id: UUID
    year_number: int = Field(ge=1, le=4)
    is_active: bool
    created_at: datetime


class EnsureAcademicYearsRequest(BaseModel):
    year_numbers: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    activate: bool = True


class MapProgramDataToYearRequest(BaseModel):
    program_code: str = Field(min_length=1)
    from_academic_year_number: int = Field(ge=1, le=4)
    to_academic_year_number: int = Field(ge=1, le=4)
    # If true, delete any existing target-year data for this program first,
    # to avoid uniqueness conflicts when remapping.
    replace_target: bool = False
    dry_run: bool = False


class MapProgramDataToYearResponse(BaseModel):
    ok: bool = True
    from_academic_year_number: int = Field(ge=1, le=4)
    to_academic_year_number: int = Field(ge=1, le=4)
    deleted: dict[str, int] = Field(default_factory=dict)
    updated: dict[str, int] = Field(default_factory=dict)
    message: str | None = None


class AdminActionResult(BaseModel):
    ok: bool = True
    created: int = 0
    updated: int = 0
    deleted: int = 0
    message: str | None = None


class CombinedSubjectGroupSectionOut(BaseModel):
    section_id: UUID
    section_code: str
    section_name: str


class CombinedSubjectGroupOut(BaseModel):
    id: UUID
    academic_year_number: int = Field(ge=1, le=4)
    subject_id: UUID
    subject_code: str
    subject_name: str
    teacher_id: UUID | None = None
    teacher_code: str | None = None
    teacher_name: str | None = None
    label: str | None = None
    sections: list[CombinedSubjectGroupSectionOut]
    created_at: datetime


class CreateCombinedSubjectGroupRequest(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    subject_code: str = Field(min_length=1)
    teacher_code: str = Field(min_length=1)
    label: str | None = None
    section_codes: list[str] = Field(default_factory=list)


class UpdateCombinedSubjectGroupRequest(BaseModel):
    teacher_code: str = Field(min_length=1)
    label: str | None = None
    section_codes: list[str] = Field(default_factory=list)


class GenerateTimeSlotsRequest(BaseModel):
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    start_time: str = Field(default="09:00")
    end_time: str = Field(default="17:00")
    slot_minutes: int = Field(default=60, ge=15, le=240)
    replace_existing: bool = False


class SetDefaultSectionWindowsRequest(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    start_slot_index: int = Field(default=0, ge=0)
    end_slot_index: int = Field(default=7, ge=0)
    replace_existing: bool = True


class ClearTimetablesRequest(BaseModel):
    # Safety guard: require explicit confirmation.
    confirm: str = Field(min_length=1)
    # Optional: clear only one program code.
    program_code: str | None = Field(default=None, min_length=1)
    # Optional: clear only one academic year (1-4). If omitted, clears all years.
    academic_year_number: int | None = Field(default=None, ge=1, le=4)


class DeleteTimetableRunRequest(BaseModel):
    # Safety guard: require explicit confirmation.
    confirm: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class AutoFixFeasibilityRequest(BaseModel):
    program_code: str = Field(min_length=1)
    # Optional: restrict auto-fix to one academic year.
    academic_year_number: int | None = Field(default=None, ge=1, le=4)

    # Ensure each required subject has at least this many eligible teachers.
    min_eligible_teachers: int = Field(default=3, ge=1, le=50)

    # If capacity is still insufficient, bump eligible teachers' caps up to these values.
    target_teacher_max_per_week: int = Field(default=35, ge=0, le=80)
    target_teacher_max_per_day: int = Field(default=6, ge=0, le=12)

    # If true, bump caps for all teachers that are eligible for required subjects,
    # even when per-subject capacity is already sufficient.
    force_bump_eligible_teacher_caps: bool = False

    dry_run: bool = False


class AutoFixFeasibilityResponse(BaseModel):
    ok: bool = True
    teacher_subjects_created: int = 0
    teachers_updated: int = 0
    subjects_inspected: int = 0
    subjects_fixed: int = 0
    subjects_unfixable: int = 0
    message: str | None = None


class TeacherSubjectSectionRefOut(BaseModel):
    section_id: UUID
    section_code: str
    section_name: str


class TeacherSubjectSectionAssignmentRow(BaseModel):
    teacher_id: UUID
    teacher_code: str | None = None
    teacher_name: str | None = None

    subject_id: UUID
    subject_code: str
    subject_name: str

    sections: list[TeacherSubjectSectionRefOut] = Field(default_factory=list)


class SetTeacherSubjectSectionsRequest(BaseModel):
    teacher_id: UUID
    subject_id: UUID
    section_ids: list[UUID] = Field(default_factory=list)


class DeleteCombinedSubjectGroupResponse(BaseModel):
    ok: bool = True
    deleted: int = 1


class ElectiveBlockSubjectOut(BaseModel):
    id: UUID
    subject_id: UUID
    subject_code: str
    subject_name: str
    subject_type: str

    teacher_id: UUID
    teacher_code: str | None = None
    teacher_name: str | None = None


class ElectiveBlockSectionOut(BaseModel):
    section_id: UUID
    section_code: str
    section_name: str


class ElectiveBlockOut(BaseModel):
    id: UUID
    academic_year_number: int = Field(ge=1, le=4)
    name: str
    code: str | None = None
    is_active: bool = True
    max_parallel_sections: int | None = None
    subjects: list[ElectiveBlockSubjectOut] = Field(default_factory=list)
    sections: list[ElectiveBlockSectionOut] = Field(default_factory=list)
    created_at: datetime


class CreateElectiveBlockRequest(BaseModel):
    program_code: str = Field(min_length=1)
    academic_year_number: int = Field(ge=1, le=4)
    name: str = Field(min_length=1)
    code: str | None = None
    is_active: bool = True
    max_parallel_sections: int | None = Field(default=None, ge=1)


class UpdateElectiveBlockRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    is_active: bool | None = None
    max_parallel_sections: int | None = Field(default=None, ge=1)


class UpsertElectiveBlockSubjectRequest(BaseModel):
    subject_id: UUID
    teacher_id: UUID


class SetElectiveBlockSectionsRequest(BaseModel):
    section_ids: list[UUID] = Field(default_factory=list)


class DeleteElectiveBlockResponse(BaseModel):
    ok: bool = True
    deleted: int = 1
