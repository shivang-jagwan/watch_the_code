from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel

from api.deps import get_tenant_id, require_admin
from api.tenant import get_by_id, where_tenant
from core.db import get_db
from models.fixed_timetable_entry import FixedTimetableEntry
from models.room import Room
from models.special_allotment import SpecialAllotment
from models.subject import Subject
from models.subject_allowed_room import SubjectAllowedRoom
from models.timetable_entry import TimetableEntry
from schemas.room import RoomCreate, RoomOut, RoomUpdate


logger = logging.getLogger(__name__)


router = APIRouter()


class RoomExclusiveSubjectUpdate(BaseModel):
    subject_id: uuid.UUID | None = None


class RoomExclusiveSubjectResponse(BaseModel):
    room_id: uuid.UUID
    subject_id: uuid.UUID | None = None


class RoomExclusiveSubjectOption(BaseModel):
    id: uuid.UUID
    code: str
    name: str


def _ensure_unique_room_code(
    db: Session,
    *,
    code: str,
    exclude_room_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
) -> None:
    q = select(Room.id).where(Room.code == code)
    if tenant_id is None:
        q = q.where(Room.tenant_id.is_(None))
    else:
        q = q.where(Room.tenant_id == tenant_id)
    if exclude_room_id is not None:
        q = q.where(Room.id != exclude_room_id)
    if db.execute(q.limit(1)).first() is not None:
        raise HTTPException(status_code=409, detail="ROOM_CODE_ALREADY_EXISTS")


def _room_in_use_flags(
    db: Session,
    *,
    room_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
) -> dict[str, bool]:
    q_runs = where_tenant(select(TimetableEntry.id).where(TimetableEntry.room_id == room_id).limit(1), TimetableEntry, tenant_id)
    used_in_runs = db.execute(q_runs).first() is not None

    q_fixed = where_tenant(
        select(FixedTimetableEntry.id).where(FixedTimetableEntry.room_id == room_id).limit(1),
        FixedTimetableEntry,
        tenant_id,
    )
    used_in_fixed = db.execute(q_fixed).first() is not None

    q_special = where_tenant(
        select(SpecialAllotment.id).where(SpecialAllotment.room_id == room_id).limit(1),
        SpecialAllotment,
        tenant_id,
    )
    used_in_special = db.execute(q_special).first() is not None
    return {
        "used_in_timetable_entries": bool(used_in_runs),
        "used_in_fixed_entries": bool(used_in_fixed),
        "used_in_special_allotments": bool(used_in_special),
    }


@router.get("/", response_model=list[RoomOut])
def list_rooms(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[RoomOut]:
    q = where_tenant(select(Room).where(Room.is_active.is_(True)), Room, tenant_id).order_by(Room.code.asc())
    rooms = db.execute(q).scalars().all()
    if not rooms:
        return []

    room_ids = [r.id for r in rooms]
    q_ex = where_tenant(
        select(SubjectAllowedRoom.room_id, SubjectAllowedRoom.subject_id)
        .where(SubjectAllowedRoom.room_id.in_(room_ids))
        .where(SubjectAllowedRoom.is_exclusive.is_(True)),
        SubjectAllowedRoom,
        tenant_id,
    )
    exclusive_subject_by_room: dict[uuid.UUID, uuid.UUID] = {}
    for room_id, subject_id in db.execute(q_ex).all():
        exclusive_subject_by_room.setdefault(room_id, subject_id)

    for room in rooms:
        setattr(room, "exclusive_subject_id", exclusive_subject_by_room.get(room.id))
    return rooms


@router.get("/exclusive-subject-options", response_model=list[RoomExclusiveSubjectOption])
def list_room_exclusive_subject_options(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> list[RoomExclusiveSubjectOption]:
    q = where_tenant(select(Subject).where(Subject.is_active.is_(True)), Subject, tenant_id).order_by(Subject.code.asc())
    rows = db.execute(q).scalars().all()
    return [RoomExclusiveSubjectOption(id=s.id, code=str(s.code), name=str(s.name)) for s in rows]


@router.post("/", response_model=RoomOut)
def create_room(
    payload: RoomCreate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> RoomOut:
    data = payload.model_dump()
    data["code"] = str(data["code"]).strip()
    data["name"] = str(data["name"]).strip()
    if data.get("special_note") is not None:
        data["special_note"] = str(data["special_note"]).strip() or None
    if not data["code"]:
        raise HTTPException(status_code=400, detail="INVALID_CODE")
    if not data["name"]:
        raise HTTPException(status_code=400, detail="INVALID_NAME")

    _ensure_unique_room_code(db, code=data["code"], exclude_room_id=None, tenant_id=tenant_id)

    if tenant_id is not None:
        data["tenant_id"] = tenant_id
    room = Room(**data)
    db.add(room)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="ROOM_CODE_ALREADY_EXISTS")
    db.refresh(room)
    return room


@router.put("/{room_id}", response_model=RoomOut)
def put_room(
    room_id: uuid.UUID,
    payload: RoomCreate,
    force: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> RoomOut:
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    data = payload.model_dump()
    data["code"] = str(data["code"]).strip()
    data["name"] = str(data["name"]).strip()
    if data.get("special_note") is not None:
        data["special_note"] = str(data["special_note"]).strip() or None
    if not data["code"]:
        raise HTTPException(status_code=400, detail="INVALID_CODE")
    if not data["name"]:
        raise HTTPException(status_code=400, detail="INVALID_NAME")

    _ensure_unique_room_code(db, code=data["code"], exclude_room_id=room_id, tenant_id=tenant_id)

    if str(room.room_type) != str(data.get("room_type")):
        used_in_runs = (
            db.execute(
                where_tenant(
                    select(TimetableEntry.id).where(TimetableEntry.room_id == room_id).limit(1),
                    TimetableEntry,
                    tenant_id,
                )
            ).first()
        )
        used_in_fixed = db.execute(
            where_tenant(
                select(FixedTimetableEntry.id).where(FixedTimetableEntry.room_id == room_id).limit(1),
                FixedTimetableEntry,
                tenant_id,
            )
        ).first()
        if used_in_runs or used_in_fixed:
            logger.warning(
                "Room type changed for room_id=%s (code=%s) but room is referenced by timetable/fixed entries",
                str(room_id),
                str(room.code),
            )

    if bool(data.get("is_special")) and not bool(getattr(room, "is_special", False)):
        flags = _room_in_use_flags(db, room_id=room_id, tenant_id=tenant_id)
        if any(flags.values()) and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROOM_IN_USE_CONFIRM_REQUIRED",
                    "errors": [
                        "Room is referenced by existing timetable/fixed/special rows.",
                        f"Usage: {flags}",
                        "Retry with ?force=true to confirm.",
                    ],
                },
            )

    room.code = data["code"]
    room.name = data["name"]
    room.room_type = data["room_type"]
    room.capacity = int(data["capacity"])
    room.is_active = bool(data["is_active"])
    room.is_special = bool(data.get("is_special"))
    room.special_note = data.get("special_note")

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="ROOM_CODE_ALREADY_EXISTS")
    db.refresh(room)
    return room


@router.patch("/{room_id}", response_model=RoomOut)
def update_room(
    room_id: uuid.UUID,
    payload: RoomUpdate,
    force: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> RoomOut:
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    updates = payload.model_dump(exclude_unset=True)
    if "code" in updates and updates.get("code") is not None:
        updates["code"] = str(updates["code"]).strip()
        if not updates["code"]:
            raise HTTPException(status_code=400, detail="INVALID_CODE")
        _ensure_unique_room_code(db, code=str(updates["code"]), exclude_room_id=room_id, tenant_id=tenant_id)
    if "name" in updates and updates.get("name") is not None:
        updates["name"] = str(updates["name"]).strip()
        if not updates["name"]:
            raise HTTPException(status_code=400, detail="INVALID_NAME")

    if "special_note" in updates and updates.get("special_note") is not None:
        updates["special_note"] = str(updates["special_note"]).strip() or None

    if "room_type" in updates and updates.get("room_type") is not None and str(room.room_type) != str(updates["room_type"]):
        used_in_runs = (
            db.execute(
                where_tenant(
                    select(TimetableEntry.id).where(TimetableEntry.room_id == room_id).limit(1),
                    TimetableEntry,
                    tenant_id,
                )
            ).first()
        )
        used_in_fixed = db.execute(
            where_tenant(
                select(FixedTimetableEntry.id).where(FixedTimetableEntry.room_id == room_id).limit(1),
                FixedTimetableEntry,
                tenant_id,
            )
        ).first()
        if used_in_runs or used_in_fixed:
            logger.warning(
                "Room type changed for room_id=%s (code=%s) but room is referenced by timetable/fixed entries",
                str(room_id),
                str(room.code),
            )

    if updates.get("is_special") is True and not bool(getattr(room, "is_special", False)):
        flags = _room_in_use_flags(db, room_id=room_id, tenant_id=tenant_id)
        if any(flags.values()) and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROOM_IN_USE_CONFIRM_REQUIRED",
                    "errors": [
                        "Room is referenced by existing timetable/fixed/special rows.",
                        f"Usage: {flags}",
                        "Retry with ?force=true to confirm.",
                    ],
                },
            )
    for k, v in updates.items():
        setattr(room, k, v)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONFLICT")
    db.refresh(room)
    return room


@router.delete("/{room_id}")
def delete_room(
    room_id: uuid.UUID,
    force: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> dict:
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    flags = _room_in_use_flags(db, room_id=room_id, tenant_id=tenant_id)
    flags["used_in_combined_groups"] = False
    flags["used_in_elective_blocks"] = False
    if any(flags.values()) and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DELETE_BLOCKED",
                "error": "Cannot delete room",
                "reason": "Used in timetable or assignments",
                "errors": [
                    "Cannot delete room",
                    "Used in timetable or assignments",
                    f"Usage: {flags}",
                    "Retry with ?force=true to delete room and dependent records",
                ],
            },
        )

    if force:
        deleted: dict[str, int] = {}

        stmt_entries = where_tenant(delete(TimetableEntry).where(TimetableEntry.room_id == room_id), TimetableEntry, tenant_id)
        deleted["timetable_entries"] = db.execute(stmt_entries).rowcount or 0

        stmt_fixed = where_tenant(delete(FixedTimetableEntry).where(FixedTimetableEntry.room_id == room_id), FixedTimetableEntry, tenant_id)
        deleted["fixed_timetable_entries"] = db.execute(stmt_fixed).rowcount or 0

        stmt_special = where_tenant(delete(SpecialAllotment).where(SpecialAllotment.room_id == room_id), SpecialAllotment, tenant_id)
        deleted["special_allotments"] = db.execute(stmt_special).rowcount or 0

        stmt_allowed = where_tenant(delete(SubjectAllowedRoom).where(SubjectAllowedRoom.room_id == room_id), SubjectAllowedRoom, tenant_id)
        deleted["subject_allowed_rooms"] = db.execute(stmt_allowed).rowcount or 0

        db.delete(room)
        db.commit()
        return {"ok": True, "force": True, "deleted": deleted}

    q_room_links = where_tenant(select(SubjectAllowedRoom).where(SubjectAllowedRoom.room_id == room_id), SubjectAllowedRoom, tenant_id)
    for row in db.execute(q_room_links).scalars().all():
        row.is_exclusive = False

    room.is_active = False
    db.commit()
    return {"ok": True, "force": False}


@router.get("/{room_id}/exclusive-subject", response_model=RoomExclusiveSubjectResponse)
def get_room_exclusive_subject(
    room_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> RoomExclusiveSubjectResponse:
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    q = where_tenant(
        select(SubjectAllowedRoom.subject_id)
        .where(SubjectAllowedRoom.room_id == room_id)
        .where(SubjectAllowedRoom.is_exclusive.is_(True))
        .limit(1),
        SubjectAllowedRoom,
        tenant_id,
    )
    row = db.execute(q).first()
    return RoomExclusiveSubjectResponse(room_id=room_id, subject_id=(row[0] if row else None))


@router.put("/{room_id}/exclusive-subject", response_model=RoomExclusiveSubjectResponse)
def put_room_exclusive_subject(
    room_id: uuid.UUID,
    payload: RoomExclusiveSubjectUpdate,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
) -> RoomExclusiveSubjectResponse:
    room = get_by_id(db, Room, room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")

    subject_id = payload.subject_id
    if subject_id is not None:
        subj = get_by_id(db, Subject, subject_id, tenant_id)
        if subj is None:
            raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    q_existing_ex = where_tenant(
        select(SubjectAllowedRoom)
        .where(SubjectAllowedRoom.room_id == room_id)
        .where(SubjectAllowedRoom.is_exclusive.is_(True)),
        SubjectAllowedRoom,
        tenant_id,
    )
    for row in db.execute(q_existing_ex).scalars().all():
        row.is_exclusive = False

    if subject_id is not None:
        q_link = where_tenant(
            select(SubjectAllowedRoom)
            .where(SubjectAllowedRoom.room_id == room_id)
            .where(SubjectAllowedRoom.subject_id == subject_id)
            .limit(1),
            SubjectAllowedRoom,
            tenant_id,
        )
        link = db.execute(q_link).scalar_one_or_none()
        if link is None:
            link = SubjectAllowedRoom(
                tenant_id=tenant_id,
                subject_id=subject_id,
                room_id=room_id,
                is_exclusive=True,
            )
            db.add(link)
        else:
            link.is_exclusive = True

    db.commit()
    return RoomExclusiveSubjectResponse(room_id=room_id, subject_id=subject_id)
