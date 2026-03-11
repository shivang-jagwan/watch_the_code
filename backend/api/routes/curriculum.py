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
from models.program import Program
from models.subject import Subject
from models.curriculum_subject import CurriculumSubject
from models.track_subject import TrackSubject
from schemas.curriculum import (
    CurriculumSubjectCreate,
    CurriculumSubjectOut,
    CurriculumSubjectUpdate,
    TrackSubjectCreate,
    TrackSubjectOut,
    TrackSubjectUpdate,
)


router = APIRouter()


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


def _get_subject(
    db: Session,
    program_id: uuid.UUID,
    academic_year_id: uuid.UUID,
    subject_code: str,
    *,
    tenant_id: uuid.UUID | None,
) -> Subject:
    q = (
        select(Subject)
        .where(Subject.program_id == program_id)
        .where(Subject.academic_year_id == academic_year_id)
        .where(Subject.code == subject_code)
    )
    q = where_tenant(q, Subject, tenant_id)
    subject = db.execute(q).scalars().first()
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")
    return subject


@router.get("/track-subjects", response_model=list[TrackSubjectOut])
def list_track_subjects(
    program_code: str = Query(min_length=1),
    academic_year_number: int = Query(ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[TrackSubjectOut]:
    program = _get_program(db, program_code, tenant_id=tenant_id)
    ay = _get_academic_year(db, int(academic_year_number), tenant_id=tenant_id)
    q = (
        select(TrackSubject)
        .where(TrackSubject.program_id == program.id)
        .where(TrackSubject.academic_year_id == ay.id)
        .order_by(TrackSubject.track.asc(), TrackSubject.created_at.asc())
    )
    q = where_tenant(q, TrackSubject, tenant_id)
    rows = db.execute(q).scalars().all()
    return rows


@router.post("/track-subjects", response_model=TrackSubjectOut)
def create_track_subject(
    payload: TrackSubjectCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TrackSubjectOut:
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)
    subject = _get_subject(db, program.id, ay.id, payload.subject_code, tenant_id=tenant_id)

    row = TrackSubject(
        tenant_id=tenant_id,
        program_id=program.id,
        academic_year_id=ay.id,
        track=payload.track,
        subject_id=subject.id,
        is_elective=payload.is_elective,
        sessions_override=payload.sessions_override,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(row)
    return row


@router.patch("/track-subjects/{track_subject_id}", response_model=TrackSubjectOut)
def update_track_subject(
    track_subject_id: uuid.UUID,
    payload: TrackSubjectUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TrackSubjectOut:
    row = get_by_id(db, TrackSubject, track_subject_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="TRACK_SUBJECT_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(row, k, v)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(row)
    return row


@router.delete("/track-subjects/{track_subject_id}")
def delete_track_subject(
    track_subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    row = get_by_id(db, TrackSubject, track_subject_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="TRACK_SUBJECT_NOT_FOUND")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ── Curriculum Subjects ────────────────────────────────────────────────────────


@router.get("/curriculum-subjects", response_model=list[CurriculumSubjectOut])
def list_curriculum_subjects(
    program_code: str = Query(min_length=1),
    academic_year_number: int = Query(ge=1, le=4),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[CurriculumSubjectOut]:
    program = _get_program(db, program_code, tenant_id=tenant_id)
    ay = _get_academic_year(db, int(academic_year_number), tenant_id=tenant_id)
    q = (
        select(CurriculumSubject)
        .where(CurriculumSubject.program_id == program.id)
        .where(CurriculumSubject.academic_year_id == ay.id)
        .order_by(CurriculumSubject.track.asc(), CurriculumSubject.created_at.asc())
    )
    q = where_tenant(q, CurriculumSubject, tenant_id)
    return db.execute(q).scalars().all()


@router.post("/curriculum-subjects", response_model=CurriculumSubjectOut)
def create_curriculum_subject(
    payload: CurriculumSubjectCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> CurriculumSubjectOut:
    program = _get_program(db, payload.program_code, tenant_id=tenant_id)
    ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)
    subject = _get_subject(db, program.id, ay.id, payload.subject_code, tenant_id=tenant_id)

    row = CurriculumSubject(
        tenant_id=tenant_id,
        program_id=program.id,
        academic_year_id=ay.id,
        track=payload.track,
        subject_id=subject.id,
        sessions_per_week=payload.sessions_per_week,
        max_per_day=payload.max_per_day,
        lab_block_size_slots=payload.lab_block_size_slots,
        is_elective=payload.is_elective,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(row)
    return row


@router.patch("/curriculum-subjects/{curriculum_subject_id}", response_model=CurriculumSubjectOut)
def update_curriculum_subject(
    curriculum_subject_id: uuid.UUID,
    payload: CurriculumSubjectUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> CurriculumSubjectOut:
    row = get_by_id(db, CurriculumSubject, curriculum_subject_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CURRICULUM_SUBJECT_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(row, k, v)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(row)
    return row


@router.delete("/curriculum-subjects/{curriculum_subject_id}")
def delete_curriculum_subject(
    curriculum_subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    row = get_by_id(db, CurriculumSubject, curriculum_subject_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CURRICULUM_SUBJECT_NOT_FOUND")
    db.delete(row)
    db.commit()
    return {"ok": True}
