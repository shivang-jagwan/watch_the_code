from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import get_by_id, where_tenant
from core.db import get_db
from models.academic_year import AcademicYear
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.elective_block_subject import ElectiveBlockSubject
from models.fixed_timetable_entry import FixedTimetableEntry
from models.program import Program
from models.room import Room
from models.special_allotment import SpecialAllotment
from models.subject import Subject
from models.subject_allowed_room import SubjectAllowedRoom
from models.timetable_entry import TimetableEntry
from schemas.subject import (
    ListSubjectAllowedRoomsResponse,
    SubjectAllowedRoomOut,
    SubjectCreate,
    SubjectOut,
    SubjectPut,
    SubjectUpdate,
)


router = APIRouter()


def _subject_usage_flags(db: Session, *, subject_id: uuid.UUID, tenant_id: uuid.UUID | None) -> dict[str, bool]:
    used_in_timetable = db.execute(
        where_tenant(
            select(TimetableEntry.id).where(TimetableEntry.subject_id == subject_id).limit(1),
            TimetableEntry,
            tenant_id,
        )
    ).first() is not None
    used_in_fixed = db.execute(
        where_tenant(
            select(FixedTimetableEntry.id).where(FixedTimetableEntry.subject_id == subject_id).where(FixedTimetableEntry.is_active.is_(True)).limit(1),
            FixedTimetableEntry,
            tenant_id,
        )
    ).first() is not None
    used_in_special = db.execute(
        where_tenant(
            select(SpecialAllotment.id).where(SpecialAllotment.subject_id == subject_id).where(SpecialAllotment.is_active.is_(True)).limit(1),
            SpecialAllotment,
            tenant_id,
        )
    ).first() is not None
    used_in_combined = db.execute(
        where_tenant(
            select(CombinedGroup.id).where(CombinedGroup.subject_id == subject_id).limit(1),
            CombinedGroup,
            tenant_id,
        )
    ).first() is not None or db.execute(
        where_tenant(
            select(CombinedGroupSection.id).where(CombinedGroupSection.subject_id == subject_id).limit(1),
            CombinedGroupSection,
            tenant_id,
        )
    ).first() is not None
    used_in_elective = db.execute(
        where_tenant(
            select(ElectiveBlockSubject.id).where(ElectiveBlockSubject.subject_id == subject_id).limit(1),
            ElectiveBlockSubject,
            tenant_id,
        )
    ).first() is not None
    return {
        "used_in_timetable_entries": used_in_timetable,
        "used_in_fixed_entries": used_in_fixed,
        "used_in_special_allotments": used_in_special,
        "used_in_combined_groups": used_in_combined,
        "used_in_elective_blocks": used_in_elective,
    }


def _validate_subject_constraints(
    *,
    subject_type: str,
    sessions_per_week: int,
    max_per_day: int,
    lab_block_size_slots: int,
) -> None:
    errors: list[str] = []

    st = str(subject_type).upper()

    if int(sessions_per_week) < 1:
        errors.append("SESSIONS_PER_WEEK_LT_1")
    if int(max_per_day) < 1:
        errors.append("MAX_PER_DAY_LT_1")
    if int(max_per_day) > int(sessions_per_week):
        errors.append("MAX_PER_DAY_GT_SESSIONS_PER_WEEK")
    if int(sessions_per_week) > 6:
        errors.append("SESSIONS_PER_WEEK_GT_6")

    if st == "THEORY":
        if int(lab_block_size_slots) != 1:
            errors.append("THEORY_LAB_BLOCK_MUST_BE_1")
    elif st == "LAB":
        if int(lab_block_size_slots) < 2:
            errors.append("LAB_BLOCK_SIZE_LT_2")
    else:
        errors.append("INVALID_SUBJECT_TYPE")

    if int(sessions_per_week) * int(lab_block_size_slots) > 12:
        errors.append("WEEKLY_SLOT_LOAD_EXCEEDS_12")

    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_SUBJECT_CONSTRAINTS",
                "errors": errors,
            },
        )


def _get_program(db: Session, program_code: str, *, tenant_id: uuid.UUID | None) -> Program:
    q = select(Program).where(Program.code == program_code)
    q = where_tenant(q, Program, tenant_id)
    program = db.execute(q).scalar_one_or_none()
    if program is None:
        raise HTTPException(status_code=404, detail="PROGRAM_NOT_FOUND")
    return program


def _get_academic_year(db: Session, year_number: int, *, tenant_id: uuid.UUID | None) -> AcademicYear:
    q = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
    q = where_tenant(q, AcademicYear, tenant_id)
    ay = db.execute(q).scalar_one_or_none()
    if ay is None:
        raise HTTPException(status_code=404, detail="ACADEMIC_YEAR_NOT_FOUND")
    return ay


def _get_or_create_academic_year(db: Session, year_number: int, *, tenant_id: uuid.UUID | None) -> AcademicYear:
    q = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
    q = where_tenant(q, AcademicYear, tenant_id)
    ay = db.execute(q).scalar_one_or_none()
    if ay is not None:
        return ay

    ay = AcademicYear(
        year_number=int(year_number),
        is_active=True,
        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
    )
    db.add(ay)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        q2 = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
        q2 = where_tenant(q2, AcademicYear, tenant_id)
        ay = db.execute(q2).scalar_one_or_none()
        if ay is None:
            raise
    return ay


@router.get("/", response_model=list[SubjectOut])
def list_subjects(
    program_code: str | None = Query(default=None),
    academic_year_number: int | None = Query(default=None, ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[SubjectOut]:
    q = where_tenant(select(Subject).where(Subject.is_active.is_(True)), Subject, tenant_id).order_by(Subject.code.asc())

    if program_code is not None:
        # For list views we prefer a clean empty result over a hard 404
        # when the program hasn't been created yet.
        q_program = where_tenant(select(Program).where(Program.code == program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            return []
        q = q.where(Subject.program_id == program.id)

    if academic_year_number is not None:
        # Same rationale as program: missing academic years are common in fresh tenants.
        q_ay = where_tenant(
            select(AcademicYear).where(AcademicYear.year_number == int(academic_year_number)),
            AcademicYear,
            tenant_id,
        )
        ay = db.execute(q_ay).scalar_one_or_none()
        if ay is None:
            return []
        q = q.where(Subject.academic_year_id == ay.id)

    return db.execute(q).scalars().all()


@router.post("/", response_model=SubjectOut)
def create_subject(
    payload: SubjectCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SubjectOut:
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    ay = _get_or_create_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

    _validate_subject_constraints(
        subject_type=payload.subject_type,
        sessions_per_week=int(payload.sessions_per_week),
        max_per_day=int(payload.max_per_day),
        lab_block_size_slots=int(payload.lab_block_size_slots),
    )

    subject = Subject(
        tenant_id=tenant_id,
        program_id=program.id,
        academic_year_id=ay.id,
        code=payload.code,
        name=payload.name,
        subject_type=payload.subject_type,
        sessions_per_week=payload.sessions_per_week,
        max_per_day=payload.max_per_day,
        lab_block_size_slots=payload.lab_block_size_slots,
        is_active=payload.is_active,
        credits=payload.credits,
    )
    db.add(subject)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(subject)
    return subject


@router.patch("/{subject_id}", response_model=SubjectOut)
def update_subject(
    subject_id: uuid.UUID,
    payload: SubjectUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SubjectOut:
    subject = get_by_id(db, Subject, subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(subject, k, v)

    if {
        "subject_type",
        "sessions_per_week",
        "max_per_day",
        "lab_block_size_slots",
    }.intersection(updates.keys()):
        _validate_subject_constraints(
            subject_type=str(subject.subject_type),
            sessions_per_week=int(subject.sessions_per_week),
            max_per_day=int(subject.max_per_day),
            lab_block_size_slots=int(subject.lab_block_size_slots),
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(subject)
    return subject


@router.put("/{subject_id}", response_model=SubjectOut)
def put_subject(
    subject_id: uuid.UUID,
    payload: SubjectPut,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SubjectOut:
    subject = get_by_id(db, Subject, subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    _validate_subject_constraints(
        subject_type=payload.subject_type,
        sessions_per_week=int(payload.sessions_per_week),
        max_per_day=int(payload.max_per_day),
        lab_block_size_slots=int(payload.lab_block_size_slots),
    )

    subject.name = payload.name
    subject.subject_type = payload.subject_type
    subject.sessions_per_week = int(payload.sessions_per_week)
    subject.max_per_day = int(payload.max_per_day)
    subject.lab_block_size_slots = int(payload.lab_block_size_slots)
    subject.is_active = bool(payload.is_active)
    subject.credits = int(payload.credits)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")

    db.refresh(subject)
    return subject


@router.delete("/{subject_id}")
def delete_subject(
    subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    subject = get_by_id(db, Subject, subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    flags = _subject_usage_flags(db, subject_id=subject_id, tenant_id=tenant_id)
    if any(flags.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DELETE_BLOCKED",
                "error": "Cannot delete subject",
                "reason": "Used in timetable or assignments",
                "errors": [
                    "Cannot delete subject",
                    "Used in timetable or assignments",
                    f"Usage: {flags}",
                ],
            },
        )

    subject.is_active = False
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Subject → Allowed Rooms endpoints
# ---------------------------------------------------------------------------


@router.get("/{subject_id}/allowed-rooms", response_model=ListSubjectAllowedRoomsResponse)
def list_subject_allowed_rooms(
    subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> ListSubjectAllowedRoomsResponse:
    subject = get_by_id(db, Subject, subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    q = select(SubjectAllowedRoom).where(SubjectAllowedRoom.subject_id == subject_id)
    q = where_tenant(q, SubjectAllowedRoom, tenant_id)
    rows = db.execute(q).scalars().all()
    exclusive_room_ids = [r.room_id for r in rows if bool(getattr(r, "is_exclusive", False))]
    return ListSubjectAllowedRoomsResponse(
        subject_id=subject_id,
        room_ids=[r.room_id for r in rows],
        exclusive_room_ids=exclusive_room_ids,
    )


@router.post("/{subject_id}/allowed-rooms", response_model=SubjectAllowedRoomOut, status_code=201)
def add_subject_allowed_room(
    subject_id: uuid.UUID,
    room_id: uuid.UUID,
    is_exclusive: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SubjectAllowedRoomOut:
    subject = get_by_id(db, Subject, subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    if bool(is_exclusive):
        q_conflict = where_tenant(
            select(SubjectAllowedRoom)
            .where(SubjectAllowedRoom.room_id == room_id)
            .where(SubjectAllowedRoom.is_exclusive.is_(True))
            .where(SubjectAllowedRoom.subject_id != subject_id)
            .limit(1),
            SubjectAllowedRoom,
            tenant_id,
        )
        conflict = db.execute(q_conflict).scalar_one_or_none()
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROOM_CONFLICT",
                    "error": "ROOM_CONFLICT",
                    "message": f"Room {room.code} assigned exclusively to multiple subjects",
                },
            )

    q_existing = where_tenant(
        select(SubjectAllowedRoom)
        .where(SubjectAllowedRoom.subject_id == subject_id)
        .where(SubjectAllowedRoom.room_id == room_id)
        .limit(1),
        SubjectAllowedRoom,
        tenant_id,
    )
    row = db.execute(q_existing).scalar_one_or_none()
    if row is None:
        row = SubjectAllowedRoom(
            tenant_id=tenant_id,
            subject_id=subject_id,
            room_id=room_id,
            is_exclusive=bool(is_exclusive),
        )
        db.add(row)
    else:
        row.is_exclusive = bool(is_exclusive)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="ALREADY_EXISTS")
    db.refresh(row)
    return SubjectAllowedRoomOut.model_validate(row)


@router.delete("/{subject_id}/allowed-rooms/{room_id}")
def remove_subject_allowed_room(
    subject_id: uuid.UUID,
    room_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    q = (
        select(SubjectAllowedRoom)
        .where(SubjectAllowedRoom.subject_id == subject_id)
        .where(SubjectAllowedRoom.room_id == room_id)
    )
    q = where_tenant(q, SubjectAllowedRoom, tenant_id)
    row = db.execute(q).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    db.delete(row)
    db.commit()
    return {"ok": True}
