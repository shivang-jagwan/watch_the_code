from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import get_by_id, where_tenant
from core.db import get_db
from models.academic_year import AcademicYear
from models.elective_block import ElectiveBlock
from models.room import Room
from models.section import Section
from models.subject import Subject
from models.teacher import Teacher
from models.time_slot import TimeSlot
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun

router = APIRouter()


@router.get("/board")
def get_manual_editor_board(
    program_code: str = Query(...),
    run_id: Optional[uuid.UUID] = Query(default=None),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: Optional[uuid.UUID] = Depends(get_tenant_id),
):
    """Load board data: entries, slots, teachers, rooms for the manual editor."""
    if run_id is not None:
        run = get_by_id(db, TimetableRun, run_id, tenant_id)
        if run is None:
            raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")
    else:
        q = where_tenant(select(TimetableRun), TimetableRun, tenant_id)
        q = q.where(TimetableRun.parameters["program_code"].astext == program_code)
        q = q.order_by(TimetableRun.created_at.desc()).limit(50)
        rows = db.execute(q).scalars().all()
        run = None
        # Prefer latest non-MANUAL_EDIT FEASIBLE/OPTIMAL
        for r in rows:
            scope = (r.parameters or {}).get("scope", "")
            if scope != "MANUAL_EDIT" and str(r.status) in {"FEASIBLE", "OPTIMAL", "SUBOPTIMAL"}:
                run = r
                break
        # Fallback: any FEASIBLE/OPTIMAL
        if run is None:
            for r in rows:
                if str(r.status) in {"FEASIBLE", "OPTIMAL", "SUBOPTIMAL"}:
                    run = r
                    break
        if run is None:
            raise HTTPException(status_code=404, detail="NO_FEASIBLE_RUN")

    # Fetch all entries for this run
    q_entries = (
        select(TimetableEntry, Section, Subject, Teacher, Room, TimeSlot, ElectiveBlock)
        .join(Section, Section.id == TimetableEntry.section_id)
        .join(Subject, Subject.id == TimetableEntry.subject_id)
        .join(Teacher, Teacher.id == TimetableEntry.teacher_id)
        .join(Room, Room.id == TimetableEntry.room_id)
        .join(TimeSlot, TimeSlot.id == TimetableEntry.slot_id)
        .outerjoin(ElectiveBlock, ElectiveBlock.id == TimetableEntry.elective_block_id)
        .where(TimetableEntry.run_id == run.id)
    )
    q_entries = where_tenant(q_entries, TimetableEntry, tenant_id)
    q_entries = q_entries.order_by(
        Section.code.asc(), TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc()
    )

    entries_out = []
    for te, sec, subj, teacher, room, slot, eb in db.execute(q_entries).all():
        entries_out.append(
            {
                "id": str(te.id),
                "run_id": str(te.run_id),
                "section_id": str(sec.id),
                "section_code": sec.code,
                "section_name": sec.name,
                "subject_id": str(subj.id),
                "subject_code": subj.code,
                "subject_name": subj.name,
                "subject_type": str(subj.subject_type),
                "teacher_id": str(teacher.id),
                "teacher_code": teacher.code,
                "teacher_name": teacher.full_name,
                "room_id": str(room.id),
                "room_code": room.code,
                "room_name": room.name,
                "room_type": str(room.room_type),
                "slot_id": str(slot.id),
                "day_of_week": int(slot.day_of_week),
                "slot_index": int(slot.slot_index),
                "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"),
                "combined_class_id": str(te.combined_class_id) if te.combined_class_id else None,
                "elective_block_id": str(te.elective_block_id) if te.elective_block_id else None,
                "elective_block_name": eb.name if eb is not None else None,
                "created_at": str(te.created_at),
            }
        )

    # Fetch all time slots
    q_slots = (
        where_tenant(select(TimeSlot), TimeSlot, tenant_id)
        .order_by(TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc())
    )
    slots_out = [
        {
            "id": str(s.id),
            "day_of_week": int(s.day_of_week),
            "slot_index": int(s.slot_index),
            "start_time": s.start_time.strftime("%H:%M"),
            "end_time": s.end_time.strftime("%H:%M"),
        }
        for s in db.execute(q_slots).scalars().all()
    ]

    # Fetch active teachers
    q_teachers = (
        where_tenant(select(Teacher), Teacher, tenant_id)
        .where(Teacher.is_active.is_(True))
        .order_by(Teacher.full_name.asc())
    )
    teachers_out = [
        {
            "id": str(t.id),
            "code": t.code,
            "full_name": t.full_name,
            "weekly_off_day": t.weekly_off_day,
        }
        for t in db.execute(q_teachers).scalars().all()
    ]

    # Fetch active rooms
    q_rooms = (
        where_tenant(select(Room), Room, tenant_id)
        .where(Room.is_active.is_(True))
        .order_by(Room.code.asc())
    )
    rooms_out = [
        {
            "id": str(r.id),
            "code": r.code,
            "name": r.name,
            "room_type": str(r.room_type),
        }
        for r in db.execute(q_rooms).scalars().all()
    ]

    return {
        "run_id": str(run.id),
        "run_status": str(run.status),
        "entries": entries_out,
        "slots": slots_out,
        "teachers": teachers_out,
        "rooms": rooms_out,
    }


class ManualSaveEntryIn(BaseModel):
    section_id: str
    subject_id: str
    teacher_id: str
    room_id: str
    slot_id: str
    combined_class_id: Optional[str] = None
    elective_block_id: Optional[str] = None


class ManualSaveRequest(BaseModel):
    source_run_id: str
    program_code: str
    entries: list[ManualSaveEntryIn]


@router.post("/save")
def save_manual_edits(
    payload: ManualSaveRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: Optional[uuid.UUID] = Depends(get_tenant_id),
):
    """Save manual timetable edits as a new TimetableRun (scope=MANUAL_EDIT)."""
    source_run = get_by_id(db, TimetableRun, uuid.UUID(payload.source_run_id), tenant_id)
    if source_run is None:
        raise HTTPException(status_code=404, detail="SOURCE_RUN_NOT_FOUND")

    if not payload.entries:
        raise HTTPException(status_code=422, detail="NO_ENTRIES_PROVIDED")

    # Build section -> academic_year_id mapping
    section_uuids = list({uuid.UUID(e.section_id) for e in payload.entries})
    q_secs = (
        where_tenant(select(Section), Section, tenant_id)
        .where(Section.id.in_(section_uuids))
    )
    sec_rows = db.execute(q_secs).scalars().all()
    sec_year_map: dict[str, uuid.UUID] = {str(s.id): s.academic_year_id for s in sec_rows}

    # Create new run
    new_run = TimetableRun(
        tenant_id=tenant_id,
        academic_year_id=None,
        status="FEASIBLE",
        seed=None,
        solver_version="manual",
        parameters={
            "scope": "MANUAL_EDIT",
            "program_code": payload.program_code,
            "source_run_id": payload.source_run_id,
        },
        notes=f"Manual edit based on run {payload.source_run_id[:8]}",
    )
    db.add(new_run)
    db.flush()

    entries_written = 0
    for e in payload.entries:
        year_id = sec_year_map.get(e.section_id)
        new_entry = TimetableEntry(
            tenant_id=tenant_id,
            run_id=new_run.id,
            academic_year_id=year_id,
            section_id=uuid.UUID(e.section_id),
            subject_id=uuid.UUID(e.subject_id),
            teacher_id=uuid.UUID(e.teacher_id),
            room_id=uuid.UUID(e.room_id),
            slot_id=uuid.UUID(e.slot_id),
            combined_class_id=uuid.UUID(e.combined_class_id) if e.combined_class_id else None,
            elective_block_id=uuid.UUID(e.elective_block_id) if e.elective_block_id else None,
        )
        db.add(new_entry)
        entries_written += 1

    db.commit()

    return {"run_id": str(new_run.id), "entries_written": entries_written}
