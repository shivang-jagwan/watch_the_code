from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import get_by_id, where_tenant
from core.db import get_db
from models.academic_year import AcademicYear
from models.program import Program
from models.room import Room
from models.section import Section
from models.section_time_window import SectionTimeWindow
from models.section_subject import SectionSubject
from models.subject import Subject
from models.time_slot import TimeSlot
from schemas.section import SectionCreate, SectionOut, SectionPut, SectionStrengthPut, SectionUpdate
from schemas.section_subject import SectionSubjectCreate
from schemas.section_time_window import (
    ListSectionTimeWindowsResponse,
    PutSectionTimeWindowsRequest,
    SectionTimeWindowOut,
)
from schemas.subject import SubjectOut


router = APIRouter()


logger = logging.getLogger(__name__)


ALLOWED_TRACKS = {"CORE", "CYBER", "AI_DS", "AI_ML"}


def _validate_track(track: str) -> str:
    t = str(track).upper().strip()
    if t not in ALLOWED_TRACKS:
        raise HTTPException(status_code=400, detail="INVALID_TRACK")
    return t


def _ensure_unique_section_code(
    db: Session,
    *,
    program_id,
    code: str,
    exclude_section_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
) -> None:
    q = select(Section.id).where(Section.program_id == program_id).where(Section.code == code)
    q = where_tenant(q, Section, tenant_id)
    if exclude_section_id is not None:
        q = q.where(Section.id != exclude_section_id)
    if db.execute(q.limit(1)).first() is not None:
        raise HTTPException(status_code=409, detail="SECTION_CODE_ALREADY_EXISTS")


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


def _active_days_from_time_slots(db: Session, *, tenant_id: uuid.UUID | None) -> list[int]:
    days = (
        db.execute(
            where_tenant(select(TimeSlot.day_of_week).distinct(), TimeSlot, tenant_id).order_by(
                TimeSlot.day_of_week.asc()
            )
        )
        .scalars()
        .all()
    )
    return [int(d) for d in days]


def _slot_indices_by_day(db: Session, *, tenant_id: uuid.UUID | None) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    rows = db.execute(where_tenant(select(TimeSlot.day_of_week, TimeSlot.slot_index), TimeSlot, tenant_id)).all()
    for d, i in rows:
        out.setdefault(int(d), set()).add(int(i))
    return out


@router.get("/", response_model=list[SectionOut])
def list_sections(
    program_code: str | None = Query(default=None),
    academic_year_number: int | None = Query(default=None, ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[SectionOut]:
    q = where_tenant(select(Section), Section, tenant_id).order_by(Section.code.asc())

    if program_code is not None:
        q_program = where_tenant(select(Program).where(Program.code == program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            return []
        q = q.where(Section.program_id == program.id)

    if academic_year_number is not None:
        q_ay = where_tenant(
            select(AcademicYear).where(AcademicYear.year_number == int(academic_year_number)),
            AcademicYear,
            tenant_id,
        )
        ay = db.execute(q_ay).scalar_one_or_none()
        if ay is None:
            return []
        q = q.where(Section.academic_year_id == ay.id)

    return db.execute(q).scalars().all()


@router.post("/", response_model=SectionOut)
def create_section(
    payload: SectionCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SectionOut:
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    ay = _get_or_create_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

    track = _validate_track(payload.track)
    _ensure_unique_section_code(
        db,
        program_id=program.id,
        code=payload.code,
        exclude_section_id=None,
        tenant_id=tenant_id,
    )

    section = Section(
        tenant_id=tenant_id,
        program_id=program.id,
        academic_year_id=ay.id,
        code=payload.code,
        name=payload.name,
        strength=payload.strength,
        track=track,
        is_active=payload.is_active,
        max_daily_slots=payload.max_daily_slots,
    )
    db.add(section)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(section)
    return section


@router.patch("/{section_id}", response_model=SectionOut)
def update_section(
    section_id: uuid.UUID,
    payload: SectionUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SectionOut:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    if "track" in updates and updates.get("track") is not None:
        updates["track"] = _validate_track(str(updates["track"]))
    if "strength" in updates and updates.get("strength") is not None and int(updates["strength"]) < 0:
        raise HTTPException(status_code=400, detail="INVALID_STRENGTH")
    if "code" in updates and updates.get("code"):
        _ensure_unique_section_code(
            db,
            program_id=section.program_id,
            code=str(updates["code"]),
            exclude_section_id=section_id,
            tenant_id=tenant_id,
        )
    for k, v in updates.items():
        setattr(section, k, v)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(section)
    return section


@router.put("/{section_id}", response_model=SectionOut)
def put_section(
    section_id: uuid.UUID,
    payload: SectionStrengthPut,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> SectionOut:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    next_strength = int(payload.strength)
    if next_strength < 0:
        raise HTTPException(status_code=400, detail="INVALID_STRENGTH")

    max_capacity = (
        db.execute(where_tenant(select(func.max(Room.capacity)).where(Room.is_active.is_(True)), Room, tenant_id))
        .scalar_one_or_none()
    )
    if max_capacity is not None and next_strength > int(max_capacity):
        logger.warning(
            "Section strength exceeds max active room capacity (section_id=%s, strength=%s, max_capacity=%s)",
            str(section_id),
            str(next_strength),
            str(max_capacity),
        )

    section.strength = next_strength

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")

    db.refresh(section)
    return section


@router.get("/{section_id}/subjects", response_model=list[SubjectOut])
def list_section_subjects(
    section_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[SubjectOut]:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    q_subject_ids = select(SectionSubject.subject_id).where(SectionSubject.section_id == section_id)
    q_subject_ids = where_tenant(q_subject_ids, SectionSubject, tenant_id)
    subject_ids = db.execute(q_subject_ids).scalars().all()
    if not subject_ids:
        return []

    q = select(Subject).where(Subject.id.in_(subject_ids))
    q = where_tenant(q, Subject, tenant_id).order_by(Subject.code.asc())
    subjects = db.execute(q).scalars().all()
    return subjects


@router.post("/{section_id}/subjects")
def add_section_subject(
    section_id: uuid.UUID,
    payload: SectionSubjectCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    subject = get_by_id(db, Subject, payload.subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    if getattr(subject, "tenant_id", None) != getattr(section, "tenant_id", None):
        raise HTTPException(status_code=400, detail="TENANT_MISMATCH")

    if subject.program_id != section.program_id:
        raise HTTPException(status_code=400, detail="SUBJECT_PROGRAM_MISMATCH")
    if getattr(subject, "academic_year_id", None) != getattr(section, "academic_year_id", None):
        raise HTTPException(status_code=400, detail="SUBJECT_ACADEMIC_YEAR_MISMATCH")
    if not bool(subject.is_active):
        raise HTTPException(status_code=400, detail="SUBJECT_NOT_ACTIVE")

    row = SectionSubject(tenant_id=tenant_id, section_id=section_id, subject_id=payload.subject_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="SECTION_SUBJECT_ALREADY_EXISTS")

    return {"ok": True}


@router.delete("/{section_id}/subjects/{subject_id}")
def delete_section_subject(
    section_id: uuid.UUID,
    subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    q_row = (
        select(SectionSubject)
        .where(SectionSubject.section_id == section_id)
        .where(SectionSubject.subject_id == subject_id)
    )
    q_row = where_tenant(q_row, SectionSubject, tenant_id)
    row = db.execute(q_row).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="SECTION_SUBJECT_NOT_FOUND")

    db.delete(row)
    db.commit()
    return {"ok": True}


@router.delete("/{section_id}")
def delete_section(
    section_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
    db.delete(section)
    db.commit()
    return {"ok": True}


@router.get("/{section_id}/time-window", response_model=ListSectionTimeWindowsResponse)
def get_section_time_windows(
    section_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    q_rows = (
        select(SectionTimeWindow)
        .where(SectionTimeWindow.section_id == section_id)
        .order_by(SectionTimeWindow.day_of_week.asc())
    )
    q_rows = where_tenant(q_rows, SectionTimeWindow, tenant_id)
    rows = db.execute(q_rows).scalars().all()
    return ListSectionTimeWindowsResponse(
        section_id=section_id,
        windows=[
            SectionTimeWindowOut(
                id=r.id,
                section_id=r.section_id,
                day_of_week=int(r.day_of_week),
                start_slot_index=int(r.start_slot_index),
                end_slot_index=int(r.end_slot_index),
                created_at=r.created_at.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
            for r in rows
        ],
    )


@router.put("/{section_id}/time-window", response_model=ListSectionTimeWindowsResponse)
def put_section_time_windows(
    section_id: uuid.UUID,
    payload: PutSectionTimeWindowsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    active_days = _active_days_from_time_slots(db, tenant_id=tenant_id)
    if not active_days:
        raise HTTPException(status_code=422, detail="MISSING_TIME_SLOTS")

    # Ensure payload provides windows for all active days.
    by_day = {}
    for w in payload.windows:
        d = int(w.day_of_week)
        if d in by_day:
            raise HTTPException(status_code=400, detail="DUPLICATE_DAY_OF_WEEK")
        by_day[d] = w

    missing_days = [d for d in active_days if d not in by_day]
    if missing_days:
        raise HTTPException(status_code=400, detail="MISSING_DAYS")

    indices_by_day = _slot_indices_by_day(db, tenant_id=tenant_id)
    for d in active_days:
        w = by_day[d]
        if int(w.end_slot_index) < int(w.start_slot_index):
            raise HTTPException(status_code=400, detail="INVALID_WINDOW_RANGE")
        valid = indices_by_day.get(d, set())
        if int(w.start_slot_index) not in valid or int(w.end_slot_index) not in valid:
            raise HTTPException(status_code=400, detail="INVALID_SLOT_INDEX")

    q_existing = select(SectionTimeWindow).where(SectionTimeWindow.section_id == section_id)
    q_existing = where_tenant(q_existing, SectionTimeWindow, tenant_id)
    existing = db.execute(q_existing).scalars().all()
    existing_map = {int(r.day_of_week): r for r in existing}

    for d in active_days:
        w = by_day[d]
        row = existing_map.get(d)
        if row is None:
            db.add(
                SectionTimeWindow(
                    tenant_id=tenant_id,
                    section_id=section_id,
                    day_of_week=d,
                    start_slot_index=int(w.start_slot_index),
                    end_slot_index=int(w.end_slot_index),
                )
            )
        else:
            row.start_slot_index = int(w.start_slot_index)
            row.end_slot_index = int(w.end_slot_index)

    db.commit()
    return get_section_time_windows(section_id, _admin=_admin, db=db, tenant_id=tenant_id)
