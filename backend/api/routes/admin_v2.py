from __future__ import annotations

import uuid

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import where_tenant
from core.db import get_db, table_exists
from models.academic_year import AcademicYear
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.combined_subject_group import CombinedSubjectGroup
from models.combined_subject_section import CombinedSubjectSection
from models.program import Program
from models.section import Section
from models.section_time_window import SectionTimeWindow
from models.special_allotment import SpecialAllotment
from models.subject import Subject
from models.teacher import Teacher
from models.teacher_subject_section import TeacherSubjectSection
from models.time_slot import TimeSlot
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun
from models.track_subject import TrackSubject
from models.elective_block import ElectiveBlock
from models.elective_block_subject import ElectiveBlockSubject
from models.section_elective_block import SectionElectiveBlock
from schemas.admin import (
    AdminActionResult,
    AcademicYearOut,
    ClearTimetablesRequest,
    CombinedSubjectGroupOut,
    CombinedSubjectGroupSectionOut,
    CreateCombinedSubjectGroupRequest,
    UpdateCombinedSubjectGroupRequest,
    CreateElectiveBlockRequest,
    DeleteCombinedSubjectGroupResponse,
    DeleteElectiveBlockResponse,
    DeleteTimetableRunRequest,
    ElectiveBlockOut,
    ElectiveBlockSectionOut,
    ElectiveBlockSubjectOut,
    GenerateTimeSlotsRequest,
    EnsureAcademicYearsRequest,
    MapProgramDataToYearRequest,
    MapProgramDataToYearResponse,
    SetElectiveBlockSectionsRequest,
    SetDefaultSectionWindowsRequest,
    SetTeacherSubjectSectionsRequest,
    TeacherSubjectSectionAssignmentRow,
    UpdateElectiveBlockRequest,
    UpsertElectiveBlockSubjectRequest,
)


router = APIRouter()


def _get_academic_year(db: Session, year_number: int, *, tenant_id: uuid.UUID | None) -> AcademicYear:
    q = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
    q = where_tenant(q, AcademicYear, tenant_id)
    year = db.execute(q).scalars().first()
    if year is None:
        raise HTTPException(status_code=404, detail="ACADEMIC_YEAR_NOT_FOUND")
    return year


def _get_or_create_academic_year(
    db: Session,
    year_number: int,
    *,
    tenant_id: uuid.UUID | None,
    activate: bool = True,
) -> AcademicYear:
    q = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
    q = where_tenant(q, AcademicYear, tenant_id)
    year = db.execute(q).scalars().first()
    if year is not None:
        if activate and not year.is_active:
            year.is_active = True
        return year

    year = AcademicYear(year_number=int(year_number), is_active=bool(activate), **({"tenant_id": tenant_id} if tenant_id is not None else {}))
    db.add(year)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # Best-effort retry in case another request created it.
        q2 = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
        q2 = where_tenant(q2, AcademicYear, tenant_id)
        year = db.execute(q2).scalars().first()
        if year is None:
            raise
    return year


def _get_program(db: Session, program_code: str, *, tenant_id: uuid.UUID | None) -> Program:
    q = select(Program).where(Program.code == program_code)
    q = where_tenant(q, Program, tenant_id)
    program = db.execute(q).scalar_one_or_none()
    if program is None:
        raise HTTPException(status_code=404, detail="PROGRAM_NOT_FOUND")
    return program


@router.get("/academic-years", response_model=list[AcademicYearOut])
def list_academic_years(
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[AcademicYearOut]:
    q = where_tenant(select(AcademicYear), AcademicYear, tenant_id).order_by(AcademicYear.year_number.asc())
    return db.execute(q).scalars().all()


@router.post("/academic-years/ensure", response_model=AdminActionResult)
def ensure_academic_years(
    payload: EnsureAcademicYearsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> AdminActionResult:
    created = 0
    updated = 0

    for n in payload.year_numbers:
        if int(n) < 1 or int(n) > 4:
            raise HTTPException(status_code=422, detail="INVALID_ACADEMIC_YEAR")

        q_before = select(AcademicYear).where(AcademicYear.year_number == int(n))
        q_before = where_tenant(q_before, AcademicYear, tenant_id)
        before = db.execute(q_before).scalars().first()

        year = _get_or_create_academic_year(db, int(n), tenant_id=tenant_id, activate=payload.activate)
        if before is None:
            created += 1
        elif payload.activate and not before.is_active and year.is_active:
            updated += 1

    db.commit()
    return AdminActionResult(ok=True, created=created, updated=updated, deleted=0)


@router.post("/programs/map-data-to-year", response_model=MapProgramDataToYearResponse)
def map_program_data_to_year(
    payload: MapProgramDataToYearRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> MapProgramDataToYearResponse:
    program = _get_program(db, payload.program_code.strip(), tenant_id=tenant_id)
    if payload.from_academic_year_number == payload.to_academic_year_number:
        raise HTTPException(status_code=422, detail="FROM_YEAR_EQUALS_TO_YEAR")

    from_year = _get_or_create_academic_year(db, int(payload.from_academic_year_number), tenant_id=tenant_id, activate=True)
    to_year = _get_or_create_academic_year(db, int(payload.to_academic_year_number), tenant_id=tenant_id, activate=True)

    deleted: dict[str, int] = {}
    updated_counts: dict[str, int] = {}

    # Helper subqueries for program scoping.
    program_subject_ids = select(Subject.id).where(Subject.program_id == program.id)
    program_section_ids = select(Section.id).where(Section.program_id == program.id)

    def _maybe_exec(stmt, *, key: str, bucket: dict[str, int]):
        if payload.dry_run:
            bucket[key] = 0
            return
        try:
            res = db.execute(stmt)
            bucket[key] = res.rowcount or 0
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "YEAR_MAPPING_CONFLICT",
                    "step": key,
                    "hint": "Try replace_target=true if target year already has data.",
                },
            )

    # Optional: clear target-year data first to avoid uniqueness conflicts.
    if payload.replace_target:
        _maybe_exec(
            delete(TimetableEntry).where(
                TimetableEntry.academic_year_id == to_year.id,
                TimetableEntry.section_id.in_(program_section_ids),
            ),
            key="timetable_entries",
            bucket=deleted,
        )
        _maybe_exec(
            delete(SpecialAllotment).where(
                SpecialAllotment.section_id.in_(
                    select(Section.id).where(Section.program_id == program.id, Section.academic_year_id == to_year.id)
                )
            ),
            key="special_allotments",
            bucket=deleted,
        )
        _maybe_exec(
            delete(SectionTimeWindow).where(
                SectionTimeWindow.section_id.in_(
                    select(Section.id).where(Section.program_id == program.id, Section.academic_year_id == to_year.id)
                )
            ),
            key="section_time_windows",
            bucket=deleted,
        )
        _maybe_exec(
            delete(TrackSubject).where(
                TrackSubject.program_id == program.id,
                TrackSubject.academic_year_id == to_year.id,
            ),
            key="track_subjects",
            bucket=deleted,
        )

        # Combined groups: clearing target-year groups for this program is safest.
        _maybe_exec(
            delete(CombinedGroup).where(
                CombinedGroup.academic_year_id == to_year.id,
                CombinedGroup.subject_id.in_(program_subject_ids),
            ),
            key="combined_groups",
            bucket=deleted,
        )

        _maybe_exec(
            delete(ElectiveBlock).where(
                ElectiveBlock.program_id == program.id,
                ElectiveBlock.academic_year_id == to_year.id,
            ),
            key="elective_blocks",
            bucket=deleted,
        )

        _maybe_exec(
            delete(Section).where(
                Section.program_id == program.id,
                Section.academic_year_id == to_year.id,
            ),
            key="sections",
            bucket=deleted,
        )
        _maybe_exec(
            delete(Subject).where(
                Subject.program_id == program.id,
                Subject.academic_year_id == to_year.id,
            ),
            key="subjects",
            bucket=deleted,
        )

    # Update core year-scoped entities.
    _maybe_exec(
        update(Subject)
        .where(Subject.program_id == program.id, Subject.academic_year_id == from_year.id)
        .values(academic_year_id=to_year.id),
        key="subjects",
        bucket=updated_counts,
    )
    _maybe_exec(
        update(Section)
        .where(Section.program_id == program.id, Section.academic_year_id == from_year.id)
        .values(academic_year_id=to_year.id),
        key="sections",
        bucket=updated_counts,
    )

    _maybe_exec(
        update(ElectiveBlock)
        .where(ElectiveBlock.program_id == program.id, ElectiveBlock.academic_year_id == from_year.id)
        .values(academic_year_id=to_year.id),
        key="elective_blocks",
        bucket=updated_counts,
    )
    _maybe_exec(
        update(TrackSubject)
        .where(TrackSubject.program_id == program.id, TrackSubject.academic_year_id == from_year.id)
        .values(academic_year_id=to_year.id),
        key="track_subjects",
        bucket=updated_counts,
    )


    _maybe_exec(
        update(CombinedGroup)
        .where(CombinedGroup.academic_year_id == from_year.id, CombinedGroup.subject_id.in_(program_subject_ids))
        .values(academic_year_id=to_year.id),
        key="combined_groups",
        bucket=updated_counts,
    )

    _maybe_exec(
        update(TimetableEntry)
        .where(TimetableEntry.academic_year_id == from_year.id, TimetableEntry.section_id.in_(program_section_ids))
        .values(academic_year_id=to_year.id),
        key="timetable_entries",
        bucket=updated_counts,
    )

    if not payload.dry_run:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="YEAR_MAPPING_CONFLICT: likely uniqueness collision. Try replace_target=true.",
            )

    return MapProgramDataToYearResponse(
        ok=True,
        from_academic_year_number=int(payload.from_academic_year_number),
        to_academic_year_number=int(payload.to_academic_year_number),
        deleted=deleted,
        updated=updated_counts,
        message="dry_run" if payload.dry_run else None,
    )


def _parse_hhmm(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=422, detail="INVALID_TIME_FORMAT")


@router.post("/time-slots/generate", response_model=AdminActionResult)
def generate_time_slots(
    payload: GenerateTimeSlotsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    start_dt = _parse_hhmm(payload.start_time)
    end_dt = _parse_hhmm(payload.end_time)
    if end_dt <= start_dt:
        raise HTTPException(status_code=422, detail="END_TIME_MUST_BE_AFTER_START_TIME")

    deleted = 0
    if payload.replace_existing:
        # time_slots are referenced by timetable_entries.slot_id (FK). Deleting would break existing runs.
        q_in_use = where_tenant(select(TimetableEntry.id).limit(1), TimetableEntry, tenant_id)
        if db.execute(q_in_use).first() is not None:
            raise HTTPException(status_code=409, detail="TIME_SLOTS_IN_USE")
        try:
            stmt_del = where_tenant(delete(TimeSlot), TimeSlot, tenant_id)
            deleted = db.execute(stmt_del).rowcount or 0
        except IntegrityError:
            raise HTTPException(status_code=409, detail="TIME_SLOTS_IN_USE")

    q_existing = select(TimeSlot).where(TimeSlot.day_of_week.in_(payload.days))
    q_existing = where_tenant(q_existing, TimeSlot, tenant_id)
    existing = db.execute(q_existing).scalars().all()
    existing_map = {(r.day_of_week, r.slot_index): r for r in existing}

    created = 0
    updated = 0
    for day in payload.days:
        slot_index = 0
        current = start_dt
        while current + timedelta(minutes=payload.slot_minutes) <= end_dt:
            nxt = current + timedelta(minutes=payload.slot_minutes)
            key = (day, slot_index)
            row = existing_map.get(key)
            if row is None:
                db.add(
                    TimeSlot(
                        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
                        day_of_week=day,
                        slot_index=slot_index,
                        start_time=current.time(),
                        end_time=nxt.time(),
                    )
                )
                created += 1
            else:
                row.start_time = current.time()
                row.end_time = nxt.time()
                updated += 1

            slot_index += 1
            current = nxt

    db.commit()
    return AdminActionResult(ok=True, created=created, updated=updated, deleted=deleted)


@router.patch("/time-slots/{slot_id}/lunch-break", response_model=AdminActionResult)
def toggle_lunch_break(
    slot_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q = where_tenant(select(TimeSlot).where(TimeSlot.id == slot_id), TimeSlot, tenant_id)
    slot = db.execute(q).scalar_one_or_none()
    if slot is None:
        raise HTTPException(status_code=404, detail="TIME_SLOT_NOT_FOUND")
    slot.is_lunch_break = not bool(slot.is_lunch_break)
    db.commit()
    return AdminActionResult(ok=True, message=f"is_lunch_break set to {slot.is_lunch_break}")


@router.post("/timetables/clear", response_model=AdminActionResult)
def clear_timetables(
    payload: ClearTimetablesRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    if payload.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=422, detail="CONFIRM_DELETE_REQUIRED")

    year_id = None
    if payload.academic_year_number is not None:
        q_year = select(AcademicYear).where(AcademicYear.year_number == payload.academic_year_number)
        q_year = where_tenant(q_year, AcademicYear, tenant_id)
        year = db.execute(q_year).scalars().first()
        if year is None:
            raise HTTPException(status_code=404, detail="ACADEMIC_YEAR_NOT_FOUND")
        year_id = year.id

    stmt = delete(TimetableRun)
    stmt = where_tenant(stmt, TimetableRun, tenant_id)
    if year_id is not None:
        stmt = stmt.where(TimetableRun.academic_year_id == year_id)

    deleted = db.execute(stmt).rowcount or 0
    db.commit()
    return AdminActionResult(ok=True, deleted=deleted)


@router.post("/timetables/runs/delete", response_model=AdminActionResult)
def delete_timetable_run(
    payload: DeleteTimetableRunRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    if payload.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=422, detail="CONFIRM_DELETE_REQUIRED")

    stmt = delete(TimetableRun).where(TimetableRun.id == payload.run_id)
    stmt = where_tenant(stmt, TimetableRun, tenant_id)
    deleted = db.execute(stmt).rowcount or 0
    if deleted == 0:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")

    db.commit()
    return AdminActionResult(ok=True, deleted=deleted)


@router.post("/section-windows/set-default", response_model=AdminActionResult)
def set_default_section_windows(
    payload: SetDefaultSectionWindowsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    year = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

    sections = (
        db.execute(
            where_tenant(
                select(Section)
                .where(Section.program_id == program.id)
                .where(Section.academic_year_id == year.id)
                .where(Section.is_active.is_(True)),
                Section,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )
    if not sections:
        raise HTTPException(status_code=404, detail="NO_ACTIVE_SECTIONS")

    deleted = 0
    section_ids = [s.id for s in sections]
    if payload.replace_existing and section_ids:
        stmt = (
            delete(SectionTimeWindow)
            .where(SectionTimeWindow.section_id.in_(section_ids))
            .where(SectionTimeWindow.day_of_week.in_(payload.days))
        )
        stmt = where_tenant(stmt, SectionTimeWindow, tenant_id)
        deleted = db.execute(stmt).rowcount or 0

    q_existing = (
        select(SectionTimeWindow)
        .where(SectionTimeWindow.section_id.in_(section_ids))
        .where(SectionTimeWindow.day_of_week.in_(payload.days))
    )
    q_existing = where_tenant(q_existing, SectionTimeWindow, tenant_id)
    existing = db.execute(q_existing).scalars().all()
    existing_map = {(r.section_id, r.day_of_week): r for r in existing}

    created = 0
    updated = 0
    for section in sections:
        for day in payload.days:
            key = (section.id, day)
            row = existing_map.get(key)
            if row is None:
                db.add(
                    SectionTimeWindow(
                        tenant_id=tenant_id,
                        section_id=section.id,
                        day_of_week=day,
                        start_slot_index=payload.start_slot_index,
                        end_slot_index=payload.end_slot_index,
                    )
                )
                created += 1
            else:
                row.start_slot_index = payload.start_slot_index
                row.end_slot_index = payload.end_slot_index
                updated += 1

    db.commit()
    return AdminActionResult(ok=True, created=created, updated=updated, deleted=deleted)


@router.get("/teacher-subject-years")
def get_teacher_subject_year_mapping_legacy(
    _admin=Depends(require_admin),
):
    raise HTTPException(status_code=410, detail="ENDPOINT_REMOVED_USE_TEACHER_SUBJECT_SECTIONS")


@router.put("/teacher-subject-years")
def set_teacher_subject_year_mapping_legacy(
    _admin=Depends(require_admin),
):
    raise HTTPException(status_code=410, detail="ENDPOINT_REMOVED_USE_TEACHER_SUBJECT_SECTIONS")


@router.get("/teacher-subject-sections", response_model=list[TeacherSubjectSectionAssignmentRow])
def list_teacher_subject_sections(
    teacher_id: uuid.UUID | None = Query(default=None),
    subject_id: uuid.UUID | None = Query(default=None),
    section_id: uuid.UUID | None = Query(default=None),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q = (
        select(TeacherSubjectSection, Teacher, Subject, Section)
        .join(Teacher, Teacher.id == TeacherSubjectSection.teacher_id)
        .join(Subject, Subject.id == TeacherSubjectSection.subject_id)
        .join(Section, Section.id == TeacherSubjectSection.section_id)
        .where(TeacherSubjectSection.is_active.is_(True))
        .where(Teacher.is_active.is_(True))
        .where(Subject.is_active.is_(True))
        .where(Section.is_active.is_(True))
    )
    q = where_tenant(q, TeacherSubjectSection, tenant_id)
    if teacher_id is not None:
        q = q.where(TeacherSubjectSection.teacher_id == teacher_id)
    if subject_id is not None:
        q = q.where(TeacherSubjectSection.subject_id == subject_id)
    if section_id is not None:
        q = q.where(TeacherSubjectSection.section_id == section_id)

    rows = db.execute(q).all()
    if not rows:
        return []

    grouped: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
    for _tss, teacher, subject, section in rows:
        key = (teacher.id, subject.id)
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "teacher_id": teacher.id,
                "teacher_code": teacher.code,
                "teacher_name": teacher.full_name,
                "subject_id": subject.id,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "sections": [],
            }
            grouped[key] = entry
        entry["sections"].append(
            {
                "section_id": section.id,
                "section_code": section.code,
                "section_name": section.name,
            }
        )

    out = sorted(grouped.values(), key=lambda item: ((item.get("teacher_code") or ""), (item.get("subject_code") or "")))
    for r in out:
        r["sections"] = sorted(r["sections"], key=lambda s: (s.get("section_code") or ""))
    return out


@router.put("/teacher-subject-sections", response_model=AdminActionResult)
def set_teacher_subject_sections(
    payload: SetTeacherSubjectSectionsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    teacher = db.execute(where_tenant(select(Teacher).where(Teacher.id == payload.teacher_id), Teacher, tenant_id)).scalars().first()
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    subject = db.execute(where_tenant(select(Subject).where(Subject.id == payload.subject_id), Subject, tenant_id)).scalars().first()
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    section_ids = list(payload.section_ids or [])
    if section_ids:
        q_sections = where_tenant(select(Section.id).where(Section.id.in_(section_ids)), Section, tenant_id)
        existing_section_ids = set(db.execute(q_sections).scalars().all())
        invalid = [sid for sid in section_ids if sid not in existing_section_ids]
        if invalid:
            raise HTTPException(status_code=422, detail="SECTION_NOT_FOUND")

    if section_ids:
        q_conflicts = (
            select(TeacherSubjectSection, Teacher, Section)
            .join(Teacher, Teacher.id == TeacherSubjectSection.teacher_id)
            .join(Section, Section.id == TeacherSubjectSection.section_id)
            .where(TeacherSubjectSection.subject_id == subject.id)
            .where(TeacherSubjectSection.section_id.in_(section_ids))
            .where(TeacherSubjectSection.teacher_id != teacher.id)
            .where(TeacherSubjectSection.is_active.is_(True))
        )
        q_conflicts = where_tenant(q_conflicts, TeacherSubjectSection, tenant_id)
        conflict_rows = db.execute(q_conflicts).all()
        if conflict_rows:
            conflicts = [
                {
                    "section_id": str(sec.id),
                    "section_code": sec.code,
                    "existing_teacher_id": str(t.id),
                    "existing_teacher_code": t.code,
                }
                for _tss, t, sec in conflict_rows
            ]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SECTION_SUBJECT_ALREADY_ASSIGNED",
                    "subject_id": str(subject.id),
                    "conflicts": conflicts,
                },
            )

    existing_rows: list[TeacherSubjectSection] = (
        db.execute(
            where_tenant(
                select(TeacherSubjectSection)
                .where(TeacherSubjectSection.teacher_id == teacher.id)
                .where(TeacherSubjectSection.subject_id == subject.id),
                TeacherSubjectSection,
                tenant_id,
            )
        ).scalars().all()
    )
    by_section = {r.section_id: r for r in existing_rows}

    created = 0
    updated = 0
    desired = set(section_ids)

    for r in existing_rows:
        if r.is_active and (r.section_id not in desired):
            r.is_active = False
            updated += 1

    for sid in desired:
        row = by_section.get(sid)
        if row is None:
            db.add(
                TeacherSubjectSection(
                    tenant_id=tenant_id,
                    teacher_id=teacher.id,
                    subject_id=subject.id,
                    section_id=sid,
                    is_active=True,
                )
            )
            created += 1
        else:
            if not row.is_active:
                row.is_active = True
                updated += 1

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SECTION_SUBJECT_ALREADY_ASSIGNED")

    return AdminActionResult(ok=True, created=created, updated=updated, message="Updated strict assignments.")


def _ensure_teacher_subject_sections(
    db: Session,
    *,
    teacher_id: uuid.UUID,
    subject_id: uuid.UUID,
    section_ids: list[uuid.UUID],
    tenant_id: uuid.UUID | None,
) -> None:
    """Force strict assignments for (teacher, subject, sections).

    This mirrors admin assignment behavior and is used by legacy combined-group
    create/update so users don't need a separate manual assignment step first.
    """

    desired = set(section_ids)
    if not desired:
        return

    # Deactivate conflicting active assignments for other teachers on the same
    # (subject, section) pairs.
    q_conflicts = (
        select(TeacherSubjectSection)
        .where(TeacherSubjectSection.subject_id == subject_id)
        .where(TeacherSubjectSection.section_id.in_(list(desired)))
        .where(TeacherSubjectSection.teacher_id != teacher_id)
        .where(TeacherSubjectSection.is_active.is_(True))
    )
    q_conflicts = where_tenant(q_conflicts, TeacherSubjectSection, tenant_id)
    for row in db.execute(q_conflicts).scalars().all():
        row.is_active = False

    # Ensure requested teacher rows exist and are active.
    q_existing = (
        select(TeacherSubjectSection)
        .where(TeacherSubjectSection.teacher_id == teacher_id)
        .where(TeacherSubjectSection.subject_id == subject_id)
    )
    q_existing = where_tenant(q_existing, TeacherSubjectSection, tenant_id)
    existing_rows = db.execute(q_existing).scalars().all()
    by_section = {r.section_id: r for r in existing_rows}

    for sid in desired:
        row = by_section.get(sid)
        if row is None:
            db.add(
                TeacherSubjectSection(
                    tenant_id=tenant_id,
                    teacher_id=teacher_id,
                    subject_id=subject_id,
                    section_id=sid,
                    is_active=True,
                )
            )
        elif not row.is_active:
            row.is_active = True


@router.get("/combined-subject-groups", response_model=list[CombinedSubjectGroupOut])
def list_combined_subject_groups(
    program_code: str = Query(min_length=1),
    academic_year_number: int = Query(ge=1, le=4),
    subject_code: str | None = Query(default=None),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    program = _get_program(db, program_code, tenant_id=tenant_id)
    year = _get_academic_year(db, int(academic_year_number), tenant_id=tenant_id)

    subj_filter_id = None
    if subject_code is not None:
        subject = (
            db.execute(
                where_tenant(
                    select(Subject)
                    .where(Subject.program_id == program.id)
                    .where(Subject.academic_year_id == year.id)
                    .where(Subject.code == subject_code),
                    Subject,
                    tenant_id,
                )
            )
            .scalars()
            .first()
        )
        if subject is None:
            return []
        subj_filter_id = subject.id

    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")

    if use_v2:
        q = (
            select(CombinedGroup, Subject, Teacher)
            .join(Subject, Subject.id == CombinedGroup.subject_id)
            .outerjoin(Teacher, Teacher.id == CombinedGroup.teacher_id)
            .where(CombinedGroup.academic_year_id == year.id)
            .where(Subject.program_id == program.id)
        )
        q = where_tenant(q, CombinedGroup, tenant_id)
        if subj_filter_id is not None:
            q = q.where(CombinedGroup.subject_id == subj_filter_id)

        groups = db.execute(q.order_by(Subject.code.asc(), CombinedGroup.created_at.asc())).all()
        if not groups:
            return []

        group_ids = [g.id for g, _subj, _teacher in groups]
        sec_q = (
            select(CombinedGroupSection.combined_group_id, Section)
            .join(Section, Section.id == CombinedGroupSection.section_id)
            .where(CombinedGroupSection.combined_group_id.in_(group_ids))
            .order_by(Section.code.asc())
        )
        sec_q = where_tenant(sec_q, CombinedGroupSection, tenant_id)
        sec_rows = db.execute(sec_q).all()

        sections_by_group: dict[uuid.UUID, list[CombinedSubjectGroupSectionOut]] = {}
        for gid, sec in sec_rows:
            sections_by_group.setdefault(gid, []).append(
                CombinedSubjectGroupSectionOut(section_id=sec.id, section_code=sec.code, section_name=sec.name)
            )

        out: list[CombinedSubjectGroupOut] = []
        for g, subj, teacher in groups:
            out.append(
                CombinedSubjectGroupOut(
                    id=g.id,
                    academic_year_number=academic_year_number,
                    subject_id=subj.id,
                    subject_code=subj.code,
                    subject_name=subj.name,
                    teacher_id=getattr(g, "teacher_id", None),
                    teacher_code=getattr(teacher, "code", None) if teacher is not None else None,
                    teacher_name=getattr(teacher, "full_name", None) if teacher is not None else None,
                    label=getattr(g, "label", None),
                    sections=sections_by_group.get(g.id, []),
                    created_at=g.created_at,
                )
            )
        return out

    # Legacy fallback
    q = (
        select(CombinedSubjectGroup, Subject)
        .join(Subject, Subject.id == CombinedSubjectGroup.subject_id)
        .where(CombinedSubjectGroup.academic_year_id == year.id)
        .where(Subject.program_id == program.id)
        .where(Subject.is_active.is_(True))
        .order_by(Subject.code.asc(), CombinedSubjectGroup.created_at.asc())
    )
    q = where_tenant(q, CombinedSubjectGroup, tenant_id)
    if subj_filter_id is not None:
        q = q.where(CombinedSubjectGroup.subject_id == subj_filter_id)

    legacy_groups = db.execute(q).all()
    if not legacy_groups:
        return []

    group_ids = [g.id for g, _subj in legacy_groups]
    sec_q = (
        select(CombinedSubjectSection.combined_group_id, Section)
        .join(Section, Section.id == CombinedSubjectSection.section_id)
        .where(CombinedSubjectSection.combined_group_id.in_(group_ids))
        .order_by(Section.code.asc())
    )
    sec_q = where_tenant(sec_q, CombinedSubjectSection, tenant_id)
    sec_rows = db.execute(sec_q).all()

    sections_by_group: dict[uuid.UUID, list[CombinedSubjectGroupSectionOut]] = {}
    section_ids: set[uuid.UUID] = set()
    for gid, sec in sec_rows:
        section_ids.add(sec.id)
        sections_by_group.setdefault(gid, []).append(
            CombinedSubjectGroupSectionOut(section_id=sec.id, section_code=sec.code, section_name=sec.name)
        )

    subj_ids = {subj.id for _g, subj in legacy_groups}
    teacher_by_sec_subj: dict[tuple[uuid.UUID, uuid.UUID], Teacher] = {}
    if section_ids and subj_ids:
        tss_q = (
            select(TeacherSubjectSection.section_id, TeacherSubjectSection.subject_id, Teacher)
            .join(Teacher, Teacher.id == TeacherSubjectSection.teacher_id)
            .where(TeacherSubjectSection.section_id.in_(list(section_ids)))
            .where(TeacherSubjectSection.subject_id.in_(list(subj_ids)))
            .where(TeacherSubjectSection.is_active.is_(True))
        )
        tss_q = where_tenant(tss_q, TeacherSubjectSection, tenant_id)
        for sec_id, subj_id, t in db.execute(tss_q).all():
            teacher_by_sec_subj[(sec_id, subj_id)] = t

    out: list[CombinedSubjectGroupOut] = []
    for g, subj in legacy_groups:
        secs = sections_by_group.get(g.id, [])
        eff_teacher: Teacher | None = None
        ok = True
        for s in secs:
            t = teacher_by_sec_subj.get((s.section_id, subj.id))
            if t is None:
                ok = False
                break
            if eff_teacher is None:
                eff_teacher = t
            elif eff_teacher.id != t.id:
                ok = False
                break
        if not ok:
            eff_teacher = None

        out.append(
            CombinedSubjectGroupOut(
                id=g.id,
                academic_year_number=academic_year_number,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                teacher_id=eff_teacher.id if eff_teacher is not None else None,
                teacher_code=eff_teacher.code if eff_teacher is not None else None,
                teacher_name=eff_teacher.full_name if eff_teacher is not None else None,
                label=None,
                sections=secs,
                created_at=g.created_at,
            )
        )
    return out


@router.post("/combined-subject-groups", response_model=CombinedSubjectGroupOut)
def create_combined_subject_group(
    payload: CreateCombinedSubjectGroupRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    year = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

    section_codes = [c.strip() for c in (payload.section_codes or []) if str(c).strip()]
    section_codes = sorted(list(dict.fromkeys(section_codes)))
    if len(section_codes) < 2:
        raise HTTPException(status_code=422, detail="MUST_SELECT_AT_LEAST_TWO_SECTIONS")

    subject = (
        db.execute(
            where_tenant(
                select(Subject)
                .where(Subject.program_id == program.id)
                .where(Subject.academic_year_id == year.id)
                .where(Subject.code == payload.subject_code)
                .where(Subject.is_active.is_(True)),
                Subject,
                tenant_id,
            )
        )
        .scalars()
        .first()
    )
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    teacher = (
        db.execute(
            where_tenant(
                select(Teacher)
                .where(Teacher.code == payload.teacher_code)
                .where(Teacher.is_active.is_(True)),
                Teacher,
                tenant_id,
            )
        )
        .scalars()
        .first()
    )
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    sections = (
        db.execute(
            where_tenant(
                select(Section)
                .where(Section.program_id == program.id)
                .where(Section.academic_year_id == year.id)
                .where(Section.code.in_(section_codes))
                .where(Section.is_active.is_(True))
                .order_by(Section.code.asc()),
                Section,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )
    if len(sections) != len(section_codes):
        raise HTTPException(status_code=422, detail="SECTION_NOT_FOUND")

    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
    if use_v2:
        group = CombinedGroup(
            tenant_id=tenant_id,
            academic_year_id=year.id,
            subject_id=subject.id,
            teacher_id=teacher.id,
            label=(payload.label.strip() if payload.label is not None and payload.label.strip() else None),
        )
        db.add(group)
        db.flush()

        for sec in sections:
            db.add(
                CombinedGroupSection(
                    tenant_id=tenant_id,
                    combined_group_id=group.id,
                    subject_id=subject.id,
                    section_id=sec.id,
                )
            )

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="SECTION_IN_MULTIPLE_COMBINED_GROUPS")

        db.refresh(group)
        return CombinedSubjectGroupOut(
            id=group.id,
            academic_year_number=payload.academic_year_number,
            subject_id=subject.id,
            subject_code=subject.code,
            subject_name=subject.name,
            teacher_id=teacher.id,
            teacher_code=teacher.code,
            teacher_name=teacher.full_name,
            label=getattr(group, "label", None),
            sections=[
                CombinedSubjectGroupSectionOut(section_id=s.id, section_code=s.code, section_name=s.name)
                for s in sections
            ],
            created_at=group.created_at,
        )

    # Legacy fallback: auto-ensure strict assignments for selected sections.
    _ensure_teacher_subject_sections(
        db,
        teacher_id=teacher.id,
        subject_id=subject.id,
        section_ids=[s.id for s in sections],
        tenant_id=tenant_id,
    )

    legacy_group = CombinedSubjectGroup(tenant_id=tenant_id, academic_year_id=year.id, subject_id=subject.id)
    db.add(legacy_group)
    try:
        db.flush()
        for sec in sections:
            db.add(CombinedSubjectSection(tenant_id=tenant_id, combined_group_id=legacy_group.id, section_id=sec.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SECTION_IN_MULTIPLE_COMBINED_GROUPS")

    db.refresh(legacy_group)

    return CombinedSubjectGroupOut(
        id=legacy_group.id,
        academic_year_number=payload.academic_year_number,
        subject_id=subject.id,
        subject_code=subject.code,
        subject_name=subject.name,
        teacher_id=teacher.id,
        teacher_code=teacher.code,
        teacher_name=teacher.full_name,
        label=None,
        sections=[
            CombinedSubjectGroupSectionOut(section_id=s.id, section_code=s.code, section_name=s.name)
            for s in sections
        ],
        created_at=legacy_group.created_at,
    )


@router.put("/combined-subject-groups/{group_id}", response_model=CombinedSubjectGroupOut)
def update_combined_subject_group(
    group_id: uuid.UUID,
    payload: UpdateCombinedSubjectGroupRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")

    if use_v2:
        group = (
            db.execute(where_tenant(select(CombinedGroup).where(CombinedGroup.id == group_id), CombinedGroup, tenant_id))
            .scalars()
            .first()
        )
        if group is None:
            raise HTTPException(status_code=404, detail="GROUP_NOT_FOUND")
    else:
        group = (
            db.execute(
                where_tenant(
                    select(CombinedSubjectGroup).where(CombinedSubjectGroup.id == group_id),
                    CombinedSubjectGroup,
                    tenant_id,
                )
            )
            .scalars()
            .first()
        )
        if group is None:
            raise HTTPException(status_code=404, detail="GROUP_NOT_FOUND")

    subject = (
        db.execute(where_tenant(select(Subject).where(Subject.id == group.subject_id), Subject, tenant_id))
        .scalars()
        .first()
    )
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    year = (
        db.execute(where_tenant(select(AcademicYear).where(AcademicYear.id == group.academic_year_id), AcademicYear, tenant_id))
        .scalars()
        .first()
    )
    if year is None:
        raise HTTPException(status_code=404, detail="ACADEMIC_YEAR_NOT_FOUND")

    teacher = (
        db.execute(
            where_tenant(
                select(Teacher)
                .where(Teacher.code == payload.teacher_code)
                .where(Teacher.is_active.is_(True)),
                Teacher,
                tenant_id,
            )
        )
        .scalars()
        .first()
    )
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    section_codes = [c.strip() for c in (payload.section_codes or []) if str(c).strip()]
    section_codes = sorted(list(dict.fromkeys(section_codes)))
    if len(section_codes) < 2:
        raise HTTPException(status_code=422, detail="MUST_SELECT_AT_LEAST_TWO_SECTIONS")

    sections = (
        db.execute(
            where_tenant(
                select(Section)
                .where(Section.program_id == subject.program_id)
                .where(Section.academic_year_id == year.id)
                .where(Section.code.in_(section_codes))
                .where(Section.is_active.is_(True))
                .order_by(Section.code.asc()),
                Section,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )
    if len(sections) != len(section_codes):
        raise HTTPException(status_code=422, detail="SECTION_NOT_FOUND")

    if use_v2:
        group.teacher_id = teacher.id
        group.label = payload.label.strip() if payload.label is not None and payload.label.strip() else None

        stmt_del = delete(CombinedGroupSection).where(CombinedGroupSection.combined_group_id == group.id)
        stmt_del = where_tenant(stmt_del, CombinedGroupSection, tenant_id)
        db.execute(stmt_del)

        for sec in sections:
            db.add(
                CombinedGroupSection(
                    tenant_id=tenant_id,
                    combined_group_id=group.id,
                    subject_id=subject.id,
                    section_id=sec.id,
                )
            )

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="SECTION_IN_MULTIPLE_COMBINED_GROUPS")

        return CombinedSubjectGroupOut(
            id=group.id,
            academic_year_number=int(getattr(year, "year_number", 0) or 0),
            subject_id=subject.id,
            subject_code=subject.code,
            subject_name=subject.name,
            teacher_id=teacher.id,
            teacher_code=teacher.code,
            teacher_name=teacher.full_name,
            label=getattr(group, "label", None),
            sections=[
                CombinedSubjectGroupSectionOut(section_id=s.id, section_code=s.code, section_name=s.name)
                for s in sections
            ],
            created_at=group.created_at,
        )

    # Legacy fallback: auto-ensure strict assignments for selected sections.
    _ensure_teacher_subject_sections(
        db,
        teacher_id=teacher.id,
        subject_id=subject.id,
        section_ids=[s.id for s in sections],
        tenant_id=tenant_id,
    )

    stmt_del = delete(CombinedSubjectSection).where(CombinedSubjectSection.combined_group_id == group.id)
    stmt_del = where_tenant(stmt_del, CombinedSubjectSection, tenant_id)
    db.execute(stmt_del)
    for sec in sections:
        db.add(CombinedSubjectSection(tenant_id=tenant_id, combined_group_id=group.id, section_id=sec.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SECTION_IN_MULTIPLE_COMBINED_GROUPS")

    return CombinedSubjectGroupOut(
        id=group.id,
        academic_year_number=int(getattr(year, "year_number", 0) or 0),
        subject_id=subject.id,
        subject_code=subject.code,
        subject_name=subject.name,
        teacher_id=teacher.id,
        teacher_code=teacher.code,
        teacher_name=teacher.full_name,
        label=None,
        sections=[
            CombinedSubjectGroupSectionOut(section_id=s.id, section_code=s.code, section_name=s.name)
            for s in sections
        ],
        created_at=group.created_at,
    )


@router.delete("/combined-subject-groups/{group_id}", response_model=DeleteCombinedSubjectGroupResponse)
def delete_combined_subject_group(
    group_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")

    if use_v2:
        stmt_links = delete(CombinedGroupSection).where(CombinedGroupSection.combined_group_id == group_id)
        stmt_links = where_tenant(stmt_links, CombinedGroupSection, tenant_id)
        deleted_links = db.execute(stmt_links).rowcount or 0

        stmt_groups = delete(CombinedGroup).where(CombinedGroup.id == group_id)
        stmt_groups = where_tenant(stmt_groups, CombinedGroup, tenant_id)
        deleted_groups = db.execute(stmt_groups).rowcount or 0
        if deleted_groups == 0:
            raise HTTPException(status_code=404, detail="GROUP_NOT_FOUND")

        db.commit()
        return DeleteCombinedSubjectGroupResponse(ok=True, deleted=deleted_groups or deleted_links or 1)

    stmt_links = delete(CombinedSubjectSection).where(CombinedSubjectSection.combined_group_id == group_id)
    stmt_links = where_tenant(stmt_links, CombinedSubjectSection, tenant_id)
    deleted_links = db.execute(stmt_links).rowcount or 0

    stmt_groups = delete(CombinedSubjectGroup).where(CombinedSubjectGroup.id == group_id)
    stmt_groups = where_tenant(stmt_groups, CombinedSubjectGroup, tenant_id)
    deleted_groups = db.execute(stmt_groups).rowcount or 0
    if deleted_groups == 0:
        raise HTTPException(status_code=404, detail="GROUP_NOT_FOUND")

    db.commit()
    return DeleteCombinedSubjectGroupResponse(ok=True, deleted=deleted_groups or deleted_links or 1)


def _build_elective_block_out(db: Session, *, block: ElectiveBlock, academic_year_number: int) -> ElectiveBlockOut:
    tenant_id = getattr(block, "tenant_id", None)

    subj_q = (
        select(ElectiveBlockSubject, Subject, Teacher)
        .join(Subject, Subject.id == ElectiveBlockSubject.subject_id)
        .join(Teacher, Teacher.id == ElectiveBlockSubject.teacher_id)
        .where(ElectiveBlockSubject.block_id == block.id)
        .order_by(Subject.code.asc(), Teacher.code.asc())
    )
    subj_q = where_tenant(subj_q, ElectiveBlockSubject, tenant_id)
    subj_rows = db.execute(subj_q).all()

    section_q = (
        select(SectionElectiveBlock, Section)
        .join(Section, Section.id == SectionElectiveBlock.section_id)
        .where(SectionElectiveBlock.block_id == block.id)
        .order_by(Section.code.asc())
    )
    section_q = where_tenant(section_q, SectionElectiveBlock, tenant_id)
    section_rows = db.execute(section_q).all()

    return ElectiveBlockOut(
        id=block.id,
        academic_year_number=int(academic_year_number),
        name=block.name,
        code=block.code,
        is_active=bool(block.is_active),
        subjects=[
            ElectiveBlockSubjectOut(
                id=_ebs.id,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                subject_type=str(subj.subject_type),
                teacher_id=teacher.id,
                teacher_code=teacher.code,
                teacher_name=teacher.full_name,
            )
            for _ebs, subj, teacher in subj_rows
        ],
        sections=[
            ElectiveBlockSectionOut(section_id=sec.id, section_code=sec.code, section_name=sec.name)
            for _seb, sec in section_rows
        ],
        created_at=block.created_at.isoformat() if getattr(block, "created_at", None) is not None else "",
    )


@router.get("/elective-blocks", response_model=list[ElectiveBlockOut])
def list_elective_blocks(
    program_code: str = Query(min_length=1),
    academic_year_number: int = Query(ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q_program = where_tenant(select(Program).where(Program.code == program_code), Program, tenant_id)
    program = db.execute(q_program).scalar_one_or_none()
    if program is None:
        return []

    q_year = where_tenant(
        select(AcademicYear).where(AcademicYear.year_number == int(academic_year_number)),
        AcademicYear,
        tenant_id,
    )
    year = db.execute(q_year).scalar_one_or_none()
    if year is None:
        return []

    q = (
        select(ElectiveBlock)
        .where(ElectiveBlock.program_id == program.id)
        .where(ElectiveBlock.academic_year_id == year.id)
        .order_by(ElectiveBlock.created_at.desc())
    )
    q = where_tenant(q, ElectiveBlock, tenant_id)
    blocks = (
        db.execute(q).scalars().all()
    )
    return [_build_elective_block_out(db, block=b, academic_year_number=academic_year_number) for b in blocks]


@router.post("/elective-blocks", response_model=ElectiveBlockOut)
def create_elective_block(
    payload: CreateElectiveBlockRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    year = _get_or_create_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id, activate=True)

    block = ElectiveBlock(
        tenant_id=tenant_id,
        program_id=program.id,
        academic_year_id=year.id,
        name=payload.name.strip(),
        code=(payload.code.strip() if payload.code else None),
        is_active=bool(payload.is_active),
    )
    db.add(block)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="ELECTIVE_BLOCK_ALREADY_EXISTS")

    return _build_elective_block_out(db, block=block, academic_year_number=payload.academic_year_number)


@router.get("/elective-blocks/{block_id}", response_model=ElectiveBlockOut)
def get_elective_block(
    block_id: uuid.UUID,
    academic_year_number: int = Query(ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q = where_tenant(select(ElectiveBlock).where(ElectiveBlock.id == block_id), ElectiveBlock, tenant_id)
    block = db.execute(q).scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="ELECTIVE_BLOCK_NOT_FOUND")
    return _build_elective_block_out(db, block=block, academic_year_number=academic_year_number)


@router.put("/elective-blocks/{block_id}", response_model=ElectiveBlockOut)
def update_elective_block(
    block_id: uuid.UUID,
    payload: UpdateElectiveBlockRequest,
    academic_year_number: int = Query(ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q = where_tenant(select(ElectiveBlock).where(ElectiveBlock.id == block_id), ElectiveBlock, tenant_id)
    block = db.execute(q).scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="ELECTIVE_BLOCK_NOT_FOUND")

    if payload.name is not None:
        block.name = payload.name.strip()
    if payload.code is not None:
        block.code = payload.code.strip() if payload.code else None
    if payload.is_active is not None:
        block.is_active = bool(payload.is_active)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="ELECTIVE_BLOCK_ALREADY_EXISTS")

    return _build_elective_block_out(db, block=block, academic_year_number=academic_year_number)


@router.delete("/elective-blocks/{block_id}", response_model=DeleteElectiveBlockResponse)
def delete_elective_block(
    block_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    stmt = delete(ElectiveBlock).where(ElectiveBlock.id == block_id)
    stmt = where_tenant(stmt, ElectiveBlock, tenant_id)
    deleted = db.execute(stmt).rowcount or 0
    db.commit()
    if deleted <= 0:
        raise HTTPException(status_code=404, detail="ELECTIVE_BLOCK_NOT_FOUND")
    return DeleteElectiveBlockResponse(ok=True, deleted=int(deleted))


@router.post("/elective-blocks/{block_id}/subjects", response_model=AdminActionResult)
def upsert_elective_block_subject(
    block_id: uuid.UUID,
    payload: UpsertElectiveBlockSubjectRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q_block = where_tenant(select(ElectiveBlock).where(ElectiveBlock.id == block_id), ElectiveBlock, tenant_id)
    block = db.execute(q_block).scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="ELECTIVE_BLOCK_NOT_FOUND")

    q_subj = where_tenant(select(Subject).where(Subject.id == payload.subject_id), Subject, tenant_id)
    subj = db.execute(q_subj).scalar_one_or_none()
    if subj is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")
    if (
        subj.program_id != block.program_id
        or subj.academic_year_id != block.academic_year_id
    ):
        raise HTTPException(status_code=422, detail="SUBJECT_OUT_OF_SCOPE")
    if str(subj.subject_type) != "THEORY":
        raise HTTPException(status_code=422, detail="ELECTIVE_BLOCK_SUBJECT_MUST_BE_THEORY")

    q_teacher = where_tenant(select(Teacher).where(Teacher.id == payload.teacher_id), Teacher, tenant_id)
    teacher = db.execute(q_teacher).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")
    if not bool(teacher.is_active):
        raise HTTPException(status_code=422, detail="TEACHER_INACTIVE")

    q_dup = (
        select(ElectiveBlockSubject.id)
        .where(ElectiveBlockSubject.block_id == block_id)
        .where(ElectiveBlockSubject.teacher_id == payload.teacher_id)
        .limit(1)
    )
    q_dup = where_tenant(q_dup, ElectiveBlockSubject, tenant_id)
    dup = db.execute(q_dup).first()

    # If this exact pair already exists, treat as a no-op.
    q_exact = (
        select(ElectiveBlockSubject)
        .where(ElectiveBlockSubject.block_id == block_id)
        .where(ElectiveBlockSubject.subject_id == payload.subject_id)
        .where(ElectiveBlockSubject.teacher_id == payload.teacher_id)
        .limit(1)
    )
    q_exact = where_tenant(q_exact, ElectiveBlockSubject, tenant_id)
    exact = db.execute(q_exact).scalars().first()
    if exact is not None:
        return AdminActionResult(ok=True, created=0, updated=0, deleted=0)

    # Enforce rule: a teacher may appear at most once per block.
    if dup is not None:
        raise HTTPException(status_code=409, detail="DUPLICATE_TEACHER_IN_BLOCK")

    # If block already mapped to sections, require eligibility for all.
    q_sec_ids = select(SectionElectiveBlock.section_id).where(SectionElectiveBlock.block_id == block_id)
    q_sec_ids = where_tenant(q_sec_ids, SectionElectiveBlock, tenant_id)
    section_ids = [sid for (sid,) in db.execute(q_sec_ids).all()]
    if section_ids:
        eligible_q = (
            select(TeacherSubjectSection.section_id)
            .where(TeacherSubjectSection.teacher_id == payload.teacher_id)
            .where(TeacherSubjectSection.subject_id == payload.subject_id)
            .where(TeacherSubjectSection.section_id.in_(section_ids))
        )
        eligible_q = where_tenant(eligible_q, TeacherSubjectSection, tenant_id)
        eligible_rows = db.execute(eligible_q).all()
        eligible = {sid for (sid,) in eligible_rows}
        if any(sid not in eligible for sid in section_ids):
            raise HTTPException(status_code=422, detail="TEACHER_NOT_ELIGIBLE_FOR_ALL_SECTIONS")

    db.add(
        ElectiveBlockSubject(
            tenant_id=tenant_id,
            block_id=block_id,
            subject_id=payload.subject_id,
            teacher_id=payload.teacher_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="DUPLICATE_TEACHER_IN_BLOCK")
    return AdminActionResult(ok=True, created=1, updated=0, deleted=0)


@router.delete("/elective-blocks/{block_id}/subjects/{assignment_id}", response_model=AdminActionResult)
def delete_elective_block_subject(
    block_id: uuid.UUID,
    assignment_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    stmt = (
        delete(ElectiveBlockSubject)
        .where(ElectiveBlockSubject.block_id == block_id)
        .where(ElectiveBlockSubject.id == assignment_id)
    )
    stmt = where_tenant(stmt, ElectiveBlockSubject, tenant_id)
    deleted = db.execute(stmt).rowcount or 0
    db.commit()
    return AdminActionResult(ok=True, created=0, updated=0, deleted=int(deleted))


@router.put("/elective-blocks/{block_id}/sections", response_model=AdminActionResult)
def set_elective_block_sections(
    block_id: uuid.UUID,
    payload: SetElectiveBlockSectionsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    q_block = where_tenant(select(ElectiveBlock).where(ElectiveBlock.id == block_id), ElectiveBlock, tenant_id)
    block = db.execute(q_block).scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="ELECTIVE_BLOCK_NOT_FOUND")

    if payload.section_ids:
        sections = (
            db.execute(where_tenant(select(Section).where(Section.id.in_(payload.section_ids)), Section, tenant_id))
            .scalars()
            .all()
        )
        found = {s.id for s in sections}
        if len(found) != len(set(payload.section_ids)):
            raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
        for s in sections:
            if (
                s.program_id != block.program_id
                or s.academic_year_id != block.academic_year_id
            ):
                raise HTTPException(status_code=422, detail="SECTION_OUT_OF_SCOPE")

    # Eligibility check for all current assignments across all target sections.
    q_assign = select(ElectiveBlockSubject).where(ElectiveBlockSubject.block_id == block_id)
    q_assign = where_tenant(q_assign, ElectiveBlockSubject, tenant_id)
    assignments = db.execute(q_assign).scalars().all()
    for a in assignments:
        if not payload.section_ids:
            continue
        eligible_q = (
            select(TeacherSubjectSection.section_id)
            .where(TeacherSubjectSection.teacher_id == a.teacher_id)
            .where(TeacherSubjectSection.subject_id == a.subject_id)
            .where(TeacherSubjectSection.section_id.in_(payload.section_ids))
        )
        eligible_q = where_tenant(eligible_q, TeacherSubjectSection, tenant_id)
        eligible_rows = db.execute(eligible_q).all()
        eligible = {sid for (sid,) in eligible_rows}
        if any(sid not in eligible for sid in payload.section_ids):
            raise HTTPException(status_code=422, detail="TEACHER_NOT_ELIGIBLE_FOR_ALL_SECTIONS")

    q_current = select(SectionElectiveBlock.section_id).where(SectionElectiveBlock.block_id == block_id)
    q_current = where_tenant(q_current, SectionElectiveBlock, tenant_id)
    current_rows = db.execute(q_current).all()
    current_ids = {sid for (sid,) in current_rows}
    desired_ids = set(payload.section_ids)

    to_add = sorted(desired_ids - current_ids)
    to_remove = sorted(current_ids - desired_ids)

    deleted = 0
    if to_remove:
        stmt = (
            delete(SectionElectiveBlock)
            .where(SectionElectiveBlock.block_id == block_id)
            .where(SectionElectiveBlock.section_id.in_(to_remove))
        )
        stmt = where_tenant(stmt, SectionElectiveBlock, tenant_id)
        deleted = db.execute(stmt).rowcount or 0

    created = 0
    for sid in to_add:
        db.add(SectionElectiveBlock(tenant_id=tenant_id, section_id=sid, block_id=block_id))
        created += 1

    db.commit()
    return AdminActionResult(ok=True, created=int(created), updated=0, deleted=int(deleted))
