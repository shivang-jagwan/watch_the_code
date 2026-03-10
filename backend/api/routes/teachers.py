from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import get_by_id, where_tenant
from core.db import get_db
from models.teacher import Teacher
from models.teacher_time_window import TeacherTimeWindow
from schemas.teacher import TeacherCreate, TeacherOut, TeacherPut, TeacherUpdate
from schemas.teacher_time_window import (
    ListTeacherTimeWindowsResponse,
    PutTeacherTimeWindowsRequest,
    TeacherTimeWindowCreate,
    TeacherTimeWindowOut,
)


router = APIRouter()


def _validate_teacher_constraints(
    *,
    weekly_off_day: int | None,
    max_per_day: int,
    max_per_week: int,
    max_continuous: int,
) -> None:
    errors: list[str] = []

    if weekly_off_day is not None and not (0 <= int(weekly_off_day) <= 5):
        errors.append("WEEKLY_OFF_DAY_OUT_OF_RANGE")
    if int(max_per_day) > 6:
        errors.append("MAX_PER_DAY_EXCEEDS_6")
    if int(max_per_week) > 36:
        errors.append("MAX_PER_WEEK_EXCEEDS_36")
    if int(max_per_day) > int(max_per_week):
        errors.append("MAX_PER_DAY_GT_MAX_PER_WEEK")
    if int(max_continuous) > int(max_per_day):
        errors.append("MAX_CONTINUOUS_GT_MAX_PER_DAY")
    if int(max_per_day) * 6 < int(max_per_week):
        errors.append("MAX_PER_DAY_TOO_LOW_FOR_WEEK")

    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_TEACHER_CONSTRAINTS",
                "errors": errors,
            },
        )


@router.get("/", response_model=list[TeacherOut])
def list_teachers(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[TeacherOut]:
    q = where_tenant(select(Teacher), Teacher, tenant_id).order_by(Teacher.full_name.asc())
    rows = db.execute(q).scalars().all()
    return rows


@router.post("/", response_model=TeacherOut)
def create_teacher(
    payload: TeacherCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TeacherOut:
    _validate_teacher_constraints(
        weekly_off_day=payload.weekly_off_day,
        max_per_day=int(payload.max_per_day),
        max_per_week=int(payload.max_per_week),
        max_continuous=int(payload.max_continuous),
    )

    data = payload.model_dump()
    if tenant_id is not None:
        data["tenant_id"] = tenant_id
    teacher = Teacher(**data)
    db.add(teacher)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="TEACHER_CODE_ALREADY_EXISTS")
    db.refresh(teacher)
    return teacher


@router.patch("/{teacher_id}", response_model=TeacherOut)
def update_teacher(
    teacher_id: uuid.UUID,
    payload: TeacherUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TeacherOut:
    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(teacher, k, v)

    if {
        "weekly_off_day",
        "max_per_day",
        "max_per_week",
        "max_continuous",
    }.intersection(updates.keys()):
        _validate_teacher_constraints(
            weekly_off_day=teacher.weekly_off_day,
            max_per_day=int(teacher.max_per_day),
            max_per_week=int(teacher.max_per_week),
            max_continuous=int(teacher.max_continuous),
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(teacher)
    return teacher


@router.put("/{teacher_id}", response_model=TeacherOut)
def put_teacher(
    teacher_id: uuid.UUID,
    payload: TeacherPut,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TeacherOut:
    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    _validate_teacher_constraints(
        weekly_off_day=payload.weekly_off_day,
        max_per_day=int(payload.max_per_day),
        max_per_week=int(payload.max_per_week),
        max_continuous=int(payload.max_continuous),
    )

    teacher.full_name = payload.full_name
    teacher.weekly_off_day = payload.weekly_off_day
    teacher.max_per_day = int(payload.max_per_day)
    teacher.max_per_week = int(payload.max_per_week)
    teacher.max_continuous = int(payload.max_continuous)
    teacher.is_active = bool(payload.is_active)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")

    db.refresh(teacher)
    return teacher


@router.delete("/{teacher_id}")
def delete_teacher(
    teacher_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")
    db.delete(teacher)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Teacher time-window endpoints
# ---------------------------------------------------------------------------


@router.get("/{teacher_id}/time-windows", response_model=ListTeacherTimeWindowsResponse)
def get_teacher_time_windows(
    teacher_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> ListTeacherTimeWindowsResponse:
    from core.db import table_exists
    if not table_exists(db, "teacher_time_windows"):
        return ListTeacherTimeWindowsResponse(teacher_id=teacher_id, windows=[])

    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    q = (
        select(TeacherTimeWindow)
        .where(TeacherTimeWindow.teacher_id == teacher_id)
        .order_by(TeacherTimeWindow.day_of_week.asc().nulls_last())
    )
    q = where_tenant(q, TeacherTimeWindow, tenant_id)
    rows = db.execute(q).scalars().all()
    return ListTeacherTimeWindowsResponse(
        teacher_id=teacher_id,
        windows=[TeacherTimeWindowOut.model_validate(r) for r in rows],
    )


@router.put("/{teacher_id}/time-windows", response_model=ListTeacherTimeWindowsResponse)
def put_teacher_time_windows(
    teacher_id: uuid.UUID,
    payload: PutTeacherTimeWindowsRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> ListTeacherTimeWindowsResponse:
    """Replace all time windows for a teacher (full replace semantics)."""
    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    # Validate: at most one window per day_of_week value (including None).
    seen_days: set = set()
    for w in payload.windows:
        key = w.day_of_week  # None is a valid unique key
        if key in seen_days:
            raise HTTPException(
                status_code=400,
                detail="DUPLICATE_DAY_IN_WINDOWS",
            )
        seen_days.add(key)

    # Delete existing windows for this teacher then insert fresh.
    q_del = select(TeacherTimeWindow).where(TeacherTimeWindow.teacher_id == teacher_id)
    q_del = where_tenant(q_del, TeacherTimeWindow, tenant_id)
    existing = db.execute(q_del).scalars().all()
    for row in existing:
        db.delete(row)
    db.flush()

    for w in payload.windows:
        db.add(
            TeacherTimeWindow(
                tenant_id=tenant_id,
                teacher_id=teacher_id,
                day_of_week=w.day_of_week,
                start_slot_index=int(w.start_slot_index),
                end_slot_index=int(w.end_slot_index),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")

    return get_teacher_time_windows(teacher_id, _admin=_admin, db=db, tenant_id=tenant_id)


@router.post("/{teacher_id}/time-windows", response_model=TeacherTimeWindowOut, status_code=201)
def create_teacher_time_window(
    teacher_id: uuid.UUID,
    payload: TeacherTimeWindowCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> TeacherTimeWindowOut:
    """Add a single time window for a teacher."""
    teacher = get_by_id(db, Teacher, teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")

    row = TeacherTimeWindow(
        tenant_id=tenant_id,
        teacher_id=teacher_id,
        day_of_week=payload.day_of_week,
        start_slot_index=int(payload.start_slot_index),
        end_slot_index=int(payload.end_slot_index),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="WINDOW_FOR_DAY_ALREADY_EXISTS",
        )
    db.refresh(row)
    return TeacherTimeWindowOut.model_validate(row)


@router.delete("/{teacher_id}/time-windows/{window_id}")
def delete_teacher_time_window(
    teacher_id: uuid.UUID,
    window_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    q = (
        select(TeacherTimeWindow)
        .where(TeacherTimeWindow.id == window_id)
        .where(TeacherTimeWindow.teacher_id == teacher_id)
    )
    q = where_tenant(q, TeacherTimeWindow, tenant_id)
    row = db.execute(q).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="WINDOW_NOT_FOUND")
    db.delete(row)
    db.commit()
    return {"ok": True}

