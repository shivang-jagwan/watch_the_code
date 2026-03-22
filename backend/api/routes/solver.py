from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, cast
from sqlalchemy.types import String
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from core.database import SessionLocal
from api.tenant import get_by_id, where_tenant
from core.config import settings
from core.db import (
    DatabaseUnavailableError,
    get_db,
    is_transient_db_connectivity_error,
    table_exists,
    validate_db_connection,
)
from models.academic_year import AcademicYear
from models.program import Program
from models.room import Room
from models.section import Section
from models.section_subject import SectionSubject
from models.section_time_window import SectionTimeWindow
from models.section_elective_block import SectionElectiveBlock
from models.subject import Subject
from models.teacher import Teacher
from models.teacher_subject_section import TeacherSubjectSection
from models.time_slot import TimeSlot
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun
from models.fixed_timetable_entry import FixedTimetableEntry
from models.special_allotment import SpecialAllotment
from models.track_subject import TrackSubject
from models.curriculum_subject import CurriculumSubject
from models.elective_block import ElectiveBlock
from models.elective_block_subject import ElectiveBlockSubject
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.combined_subject_group import CombinedSubjectGroup
from models.combined_subject_section import CombinedSubjectSection
from schemas.solver import (
    GenerateTimetableRequest,
    GenerateGlobalTimetableRequest,
    GenerateTimetableResponse,
    ListRunConflictsResponse,
    ListRunEntriesResponse,
    ListRunsResponse,
    RunDetail,
    RunSummary,
    ListTimeSlotsResponse,
    SolveTimetableRequest,
    SolveGlobalTimetableRequest,
    SolveTimetableResponse,
    SolverConflict,
    TimetableEntryOut,
    TimeSlotOut,
    FixedTimetableEntryOut,
    ListFixedTimetableEntriesResponse,
    UpsertFixedTimetableEntryRequest,
    SpecialAllotmentOut,
    ListSpecialAllotmentsResponse,
    UpsertSpecialAllotmentRequest,
    ValidateTimetableRequest,
    ValidateTimetableResponse,
    ValidationIssue,
)
from schemas.subject import SubjectOut
from services.solver_validation import validate_prereqs
from solver.cp_sat_solver import SolverInvariantError, solve_program_global, solve_program_year
from solver.capacity_analyzer import build_capacity_data, analyze_capacity
from solver.hybrid.db_pipeline import run_and_persist_dual_solver


router = APIRouter()

logger = logging.getLogger(__name__)


def _required_subject_ids_for_section(
    db: Session,
    *,
    program_id: uuid.UUID,
    section: Section,
    tenant_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    # Explicit mapping overrides any curriculum inference.
    q_mapped = select(SectionSubject.subject_id).where(SectionSubject.section_id == section.id)
    q_mapped = where_tenant(q_mapped, SectionSubject, tenant_id)
    mapped = db.execute(q_mapped).scalars().all()
    if mapped:
        return list(mapped)

    # Curriculum (+ elective blocks). Prefer refactored curriculum_subjects with legacy fallback.
    subject_ids: list[uuid.UUID] = []
    if table_exists(db, "curriculum_subjects"):
        q_curr = (
            select(CurriculumSubject)
            .where(CurriculumSubject.program_id == program_id)
            .where(CurriculumSubject.academic_year_id == section.academic_year_id)
            .where(CurriculumSubject.track == section.track)
        )
        q_curr = where_tenant(q_curr, CurriculumSubject, tenant_id)
        curr_rows = db.execute(q_curr).scalars().all()
        mandatory = [r for r in curr_rows if not bool(getattr(r, "is_elective", False))]
        subject_ids = [r.subject_id for r in mandatory]
    else:
        q_track = (
            select(TrackSubject)
            .where(TrackSubject.program_id == program_id)
            .where(TrackSubject.academic_year_id == section.academic_year_id)
            .where(TrackSubject.track == section.track)
        )
        q_track = where_tenant(q_track, TrackSubject, tenant_id)
        track_rows = db.execute(q_track).scalars().all()
        mandatory = [r for r in track_rows if not bool(getattr(r, "is_elective", False))]
        subject_ids = [r.subject_id for r in mandatory]

    # Add elective block subjects for this section (parallel electives).
    q_blocks = select(SectionElectiveBlock.block_id).where(SectionElectiveBlock.section_id == section.id)
    q_blocks = where_tenant(q_blocks, SectionElectiveBlock, tenant_id)
    block_ids = [bid for (bid,) in db.execute(q_blocks).all()]
    if block_ids:
        q_bsub = (
            select(ElectiveBlockSubject.subject_id)
            .where(ElectiveBlockSubject.block_id.in_(block_ids))
        )
        q_bsub = where_tenant(q_bsub, ElectiveBlockSubject, tenant_id)
        for sid in db.execute(q_bsub).scalars().all():
            subject_ids.append(sid)
    return subject_ids


def _validate_fixed_entry_refs(
    db: Session,
    *,
    section: Section,
    subject: Subject,
    teacher: Teacher,
    room: Room,
    slot: TimeSlot,
    tenant_id: uuid.UUID | None,
    allow_special_room: bool = False,
) -> None:
    if subject.program_id != section.program_id:
        raise HTTPException(status_code=400, detail="SUBJECT_PROGRAM_MISMATCH")
    if getattr(subject, "academic_year_id", None) != getattr(section, "academic_year_id", None):
        raise HTTPException(status_code=400, detail="SUBJECT_ACADEMIC_YEAR_MISMATCH")
    if not bool(subject.is_active):
        raise HTTPException(status_code=400, detail="SUBJECT_NOT_ACTIVE")
    if not bool(teacher.is_active):
        raise HTTPException(status_code=400, detail="TEACHER_NOT_ACTIVE")
    if not bool(room.is_active):
        raise HTTPException(status_code=400, detail="ROOM_NOT_ACTIVE")
    if not bool(allow_special_room) and bool(getattr(room, "is_special", False)):
        raise HTTPException(status_code=400, detail="ROOM_IS_SPECIAL")

    # Section must be working at this slot (time window).
    q_w = (
        select(SectionTimeWindow)
        .where(SectionTimeWindow.section_id == section.id)
        .where(SectionTimeWindow.day_of_week == int(slot.day_of_week))
    )
    q_w = where_tenant(q_w, SectionTimeWindow, tenant_id)
    w = db.execute(q_w).scalars().first()
    if w is None:
        raise HTTPException(status_code=400, detail="SLOT_OUTSIDE_SECTION_WINDOW")
    if int(slot.slot_index) < int(w.start_slot_index) or int(slot.slot_index) > int(w.end_slot_index):
        raise HTTPException(status_code=400, detail="SLOT_OUTSIDE_SECTION_WINDOW")

    # Teacher off-day must not be violated.
    if teacher.weekly_off_day is not None and int(teacher.weekly_off_day) == int(slot.day_of_week):
        raise HTTPException(status_code=400, detail="TEACHER_WEEKLY_OFF_DAY")

    # Strict assignment: teacher must be assigned to (section, subject).
    assigned = (
        db.execute(
            where_tenant(
                select(TeacherSubjectSection.id)
                .where(TeacherSubjectSection.teacher_id == teacher.id)
                .where(TeacherSubjectSection.subject_id == subject.id)
                .where(TeacherSubjectSection.section_id == section.id)
                .where(TeacherSubjectSection.is_active.is_(True))
                .limit(1),
                TeacherSubjectSection,
                tenant_id,
            )
        ).first()
        is not None
    )
    if not assigned:
        raise HTTPException(status_code=400, detail="TEACHER_NOT_ASSIGNED_TO_SECTION_SUBJECT")

    # LAB block must fit (entry represents LAB start).
    if str(subject.subject_type) == "LAB":
        block = int(getattr(subject, "lab_block_size_slots", 1) or 1)
        if block < 1:
            block = 1

        # Need a window that covers the full block on the same day.
        end_idx = int(slot.slot_index) + block - 1
        if end_idx > int(w.end_slot_index):
            raise HTTPException(status_code=400, detail="LAB_BLOCK_DOES_NOT_FIT")

        # Ensure all covered time slots exist (contiguous indices).
        for j in range(block):
            si = int(slot.slot_index) + j
            exists = (
                db.execute(
                    where_tenant(
                        select(TimeSlot.id)
                        .where(TimeSlot.day_of_week == int(slot.day_of_week))
                        .where(TimeSlot.slot_index == int(si))
                        .limit(1),
                        TimeSlot,
                        tenant_id,
                    )
                ).first()
                is not None
            )
            if not exists:
                raise HTTPException(status_code=400, detail="LAB_BLOCK_SLOT_MISSING")


def _validate_special_allotment_refs(
    db: Session,
    *,
    section: Section,
    subject: Subject,
    teacher: Teacher,
    room: Room,
    slot: TimeSlot,
    tenant_id: uuid.UUID | None,
    existing_id: uuid.UUID | None = None,
) -> None:
    # Basic reference and feasibility checks are identical to fixed entries.
    _validate_fixed_entry_refs(
        db,
        section=section,
        subject=subject,
        teacher=teacher,
        room=room,
        slot=slot,
        tenant_id=tenant_id,
        allow_special_room=True,
    )

    if not bool(getattr(room, "is_special", False)):
        raise HTTPException(status_code=400, detail="SPECIAL_ALLOTMENT_ROOM_NOT_SPECIAL")

    # Not supported: special locks for elective blocks (would need to lock the entire block).
    use_elective_blocks = (
        table_exists(db, "elective_blocks")
        and table_exists(db, "elective_block_subjects")
        and table_exists(db, "section_elective_blocks")
    )
    in_block = False
    if use_elective_blocks:
        in_block = (
            db.execute(
                where_tenant(
                    where_tenant(
                        select(SectionElectiveBlock.id)
                        .join(ElectiveBlockSubject, ElectiveBlockSubject.block_id == SectionElectiveBlock.block_id)
                        .where(SectionElectiveBlock.section_id == section.id)
                        .where(ElectiveBlockSubject.subject_id == subject.id)
                        .limit(1),
                        SectionElectiveBlock,
                        tenant_id,
                    ),
                    ElectiveBlockSubject,
                    tenant_id,
                )
            ).first()
            is not None
        )
    if in_block:
        raise HTTPException(status_code=400, detail="SUBJECT_IN_ELECTIVE_BLOCK_NOT_SUPPORTED")

    # Not supported: special locks for combined-class subjects.
    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
    if use_v2:
        in_combined = (
            db.execute(
                where_tenant(
                    where_tenant(
                        select(CombinedGroup.id)
                        .join(CombinedGroupSection, CombinedGroupSection.combined_group_id == CombinedGroup.id)
                        .where(CombinedGroup.subject_id == subject.id)
                        .where(CombinedGroupSection.section_id == section.id)
                        .limit(1),
                        CombinedGroup,
                        tenant_id,
                    ),
                    CombinedGroupSection,
                    tenant_id,
                )
            ).first()
            is not None
        )
    else:
        in_combined = (
            db.execute(
                where_tenant(
                    where_tenant(
                        select(CombinedSubjectGroup.id)
                        .join(
                            CombinedSubjectSection,
                            CombinedSubjectSection.combined_group_id == CombinedSubjectGroup.id,
                        )
                        .where(CombinedSubjectGroup.subject_id == subject.id)
                        .where(CombinedSubjectSection.section_id == section.id)
                        .limit(1),
                        CombinedSubjectGroup,
                        tenant_id,
                    ),
                    CombinedSubjectSection,
                    tenant_id,
                )
            ).first()
            is not None
        )
    if in_combined:
        raise HTTPException(status_code=400, detail="SUBJECT_IN_COMBINED_CLASS_NOT_SUPPORTED")

    # Disallow placing a special allotment where a fixed entry already exists.
    fixed_exists = (
        db.execute(
            where_tenant(
                select(FixedTimetableEntry.id)
                .where(FixedTimetableEntry.section_id == section.id)
                .where(FixedTimetableEntry.slot_id == slot.id)
                .where(FixedTimetableEntry.is_active.is_(True))
                .limit(1),
                FixedTimetableEntry,
                tenant_id,
            )
        ).first()
        is not None
    )
    if fixed_exists:
        raise HTTPException(status_code=400, detail="SLOT_HAS_FIXED_ENTRY")

    # Teacher/room uniqueness across the whole program per-slot (active only).
    q_teacher = (
        select(SpecialAllotment.id)
        .where(SpecialAllotment.teacher_id == teacher.id)
        .where(SpecialAllotment.slot_id == slot.id)
        .where(SpecialAllotment.is_active.is_(True))
    )
    q_teacher = where_tenant(q_teacher, SpecialAllotment, tenant_id)
    if existing_id is not None:
        q_teacher = q_teacher.where(SpecialAllotment.id != existing_id)
    if db.execute(q_teacher.limit(1)).first() is not None:
        raise HTTPException(status_code=400, detail="SPECIAL_ALLOTMENT_TEACHER_SLOT_CONFLICT")

    q_room = (
        select(SpecialAllotment.id)
        .where(SpecialAllotment.room_id == room.id)
        .where(SpecialAllotment.slot_id == slot.id)
        .where(SpecialAllotment.is_active.is_(True))
    )
    q_room = where_tenant(q_room, SpecialAllotment, tenant_id)
    if existing_id is not None:
        q_room = q_room.where(SpecialAllotment.id != existing_id)
    if db.execute(q_room.limit(1)).first() is not None:
        raise HTTPException(status_code=400, detail="SPECIAL_ALLOTMENT_ROOM_SLOT_CONFLICT")


@router.get("/section-required-subjects", response_model=list[SubjectOut])
def list_required_subjects_for_section(
    section_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    subject_ids = _required_subject_ids_for_section(db, program_id=section.program_id, section=section, tenant_id=tenant_id)
    if not subject_ids:
        return []

    q = select(Subject).where(Subject.id.in_(subject_ids))
    q = where_tenant(q, Subject, tenant_id).order_by(Subject.code.asc())
    subjects = db.execute(q).scalars().all()
    return subjects


@router.get("/assigned-teacher")
def get_assigned_teacher(
    section_id: uuid.UUID,
    subject_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    """Return the strictly assigned teacher for a section+subject (if any)."""
    if get_by_id(db, Section, section_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
    if get_by_id(db, Subject, subject_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")

    q = (
        select(Teacher)
        .join(TeacherSubjectSection, TeacherSubjectSection.teacher_id == Teacher.id)
        .where(TeacherSubjectSection.section_id == section_id)
        .where(TeacherSubjectSection.subject_id == subject_id)
        .where(TeacherSubjectSection.is_active.is_(True))
        .where(Teacher.is_active.is_(True))
        .limit(1)
    )
    q = where_tenant(q, TeacherSubjectSection, tenant_id)
    q = where_tenant(q, Teacher, tenant_id)
    row = db.execute(q).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="TEACHER_ASSIGNMENT_NOT_FOUND")
    return {
        "teacher_id": row.id,
        "teacher_code": row.code,
        "teacher_name": row.full_name,
        "weekly_off_day": int(row.weekly_off_day) if row.weekly_off_day is not None else None,
    }


@router.get("/fixed-entries", response_model=ListFixedTimetableEntriesResponse)
def list_fixed_entries(
    section_id: uuid.UUID,
    include_inactive: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    q = (
        select(FixedTimetableEntry, Section, Subject, Teacher, Room, TimeSlot)
        .join(Section, Section.id == FixedTimetableEntry.section_id)
        .join(Subject, Subject.id == FixedTimetableEntry.subject_id)
        .join(Teacher, Teacher.id == FixedTimetableEntry.teacher_id)
        .join(Room, Room.id == FixedTimetableEntry.room_id)
        .join(TimeSlot, TimeSlot.id == FixedTimetableEntry.slot_id)
        .where(FixedTimetableEntry.section_id == section_id)
    )
    q = where_tenant(q, FixedTimetableEntry, tenant_id)
    if tenant_id is None:
        q = q.where(Section.tenant_id.is_(None), Subject.tenant_id.is_(None), Teacher.tenant_id.is_(None), Room.tenant_id.is_(None))
    else:
        q = q.where(Section.tenant_id == tenant_id, Subject.tenant_id == tenant_id, Teacher.tenant_id == tenant_id, Room.tenant_id == tenant_id)
    if not include_inactive:
        q = q.where(FixedTimetableEntry.is_active.is_(True))
    q = q.order_by(TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc())

    rows = db.execute(q).all()
    out: list[FixedTimetableEntryOut] = []
    for fe, sec, subj, teacher, room, slot in rows:
        out.append(
            FixedTimetableEntryOut(
                id=fe.id,
                section_id=sec.id,
                section_code=sec.code,
                section_name=sec.name,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                subject_type=str(subj.subject_type),
                teacher_id=teacher.id,
                teacher_code=teacher.code,
                teacher_name=teacher.full_name,
                room_id=room.id,
                room_code=room.code,
                room_name=room.name,
                room_type=str(room.room_type),
                slot_id=slot.id,
                day_of_week=int(slot.day_of_week),
                slot_index=int(slot.slot_index),
                start_time=slot.start_time.strftime("%H:%M"),
                end_time=slot.end_time.strftime("%H:%M"),
                is_active=bool(fe.is_active),
                created_at=fe.created_at,
            )
        )
    return ListFixedTimetableEntriesResponse(entries=out)


@router.post("/fixed-entries", response_model=FixedTimetableEntryOut)
def upsert_fixed_entry(
    payload: UpsertFixedTimetableEntryRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, payload.section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
    subject = get_by_id(db, Subject, payload.subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")
    teacher = get_by_id(db, Teacher, payload.teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")
    room = get_by_id(db, Room, payload.room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")
    slot = get_by_id(db, TimeSlot, payload.slot_id, tenant_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="SLOT_NOT_FOUND")

    allowed_subject_ids = set(
        _required_subject_ids_for_section(db, program_id=section.program_id, section=section, tenant_id=tenant_id)
    )
    if allowed_subject_ids and subject.id not in allowed_subject_ids:
        raise HTTPException(status_code=400, detail="SUBJECT_NOT_ALLOWED_FOR_SECTION")

    # Prevent a fixed entry from being placed where a special allotment already exists.
    special_exists = (
        db.execute(
            where_tenant(
                select(SpecialAllotment.id)
                .where(SpecialAllotment.section_id == payload.section_id)
                .where(SpecialAllotment.slot_id == payload.slot_id)
                .where(SpecialAllotment.is_active.is_(True))
                .limit(1),
                SpecialAllotment,
                tenant_id,
            )
        ).first()
        is not None
    )
    if special_exists:
        raise HTTPException(status_code=400, detail="SLOT_HAS_SPECIAL_ALLOTMENT")

    _validate_fixed_entry_refs(
        db,
        section=section,
        subject=subject,
        teacher=teacher,
        room=room,
        slot=slot,
        tenant_id=tenant_id,
    )

    # Upsert by (section, slot)
    existing = (
        db.execute(
            where_tenant(
                select(FixedTimetableEntry)
                .where(FixedTimetableEntry.section_id == payload.section_id)
                .where(FixedTimetableEntry.slot_id == payload.slot_id)
                .where(FixedTimetableEntry.is_active.is_(True)),
                FixedTimetableEntry,
                tenant_id,
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        existing = FixedTimetableEntry(
            tenant_id=tenant_id,
            section_id=payload.section_id,
            subject_id=payload.subject_id,
            teacher_id=payload.teacher_id,
            room_id=payload.room_id,
            slot_id=payload.slot_id,
            is_active=True,
        )
        db.add(existing)
    else:
        existing.subject_id = payload.subject_id
        existing.teacher_id = payload.teacher_id
        existing.room_id = payload.room_id

    db.commit()

    # Re-read via join for output.
    row = (
        db.execute(
            where_tenant(
                select(FixedTimetableEntry, Section, Subject, Teacher, Room, TimeSlot)
                .join(Section, Section.id == FixedTimetableEntry.section_id)
                .join(Subject, Subject.id == FixedTimetableEntry.subject_id)
                .join(Teacher, Teacher.id == FixedTimetableEntry.teacher_id)
                .join(Room, Room.id == FixedTimetableEntry.room_id)
                .join(TimeSlot, TimeSlot.id == FixedTimetableEntry.slot_id)
                .where(FixedTimetableEntry.id == existing.id),
                FixedTimetableEntry,
                tenant_id,
            )
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=500, detail="FIXED_ENTRY_WRITE_FAILED")
    fe, sec, subj, teacher, room, slot = row
    return FixedTimetableEntryOut(
        id=fe.id,
        section_id=sec.id,
        section_code=sec.code,
        section_name=sec.name,
        subject_id=subj.id,
        subject_code=subj.code,
        subject_name=subj.name,
        subject_type=str(subj.subject_type),
        teacher_id=teacher.id,
        teacher_code=teacher.code,
        teacher_name=teacher.full_name,
        room_id=room.id,
        room_code=room.code,
        room_name=room.name,
        room_type=str(room.room_type),
        slot_id=slot.id,
        day_of_week=int(slot.day_of_week),
        slot_index=int(slot.slot_index),
        start_time=slot.start_time.strftime("%H:%M"),
        end_time=slot.end_time.strftime("%H:%M"),
        is_active=bool(fe.is_active),
        created_at=fe.created_at,
    )


@router.delete("/fixed-entries/{entry_id}")
def delete_fixed_entry(
    entry_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    row = get_by_id(db, FixedTimetableEntry, entry_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="FIXED_ENTRY_NOT_FOUND")
    row.is_active = False
    db.commit()
    return {"ok": True}


@router.get("/special-allotments", response_model=ListSpecialAllotmentsResponse)
def list_special_allotments(
    section_id: uuid.UUID,
    include_inactive: bool = Query(default=False),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")

    q = (
        select(SpecialAllotment, Section, Subject, Teacher, Room, TimeSlot)
        .join(Section, Section.id == SpecialAllotment.section_id)
        .join(Subject, Subject.id == SpecialAllotment.subject_id)
        .join(Teacher, Teacher.id == SpecialAllotment.teacher_id)
        .join(Room, Room.id == SpecialAllotment.room_id)
        .join(TimeSlot, TimeSlot.id == SpecialAllotment.slot_id)
        .where(SpecialAllotment.section_id == section_id)
    )
    q = where_tenant(q, SpecialAllotment, tenant_id)
    if not include_inactive:
        q = q.where(SpecialAllotment.is_active.is_(True))
    q = q.order_by(TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc())

    rows = db.execute(q).all()
    out: list[SpecialAllotmentOut] = []
    for sa, sec, subj, teacher, room, slot in rows:
        out.append(
            SpecialAllotmentOut(
                id=sa.id,
                section_id=sec.id,
                section_code=sec.code,
                section_name=sec.name,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                subject_type=str(subj.subject_type),
                teacher_id=teacher.id,
                teacher_code=teacher.code,
                teacher_name=teacher.full_name,
                room_id=room.id,
                room_code=room.code,
                room_name=room.name,
                room_type=str(room.room_type),
                slot_id=slot.id,
                day_of_week=int(slot.day_of_week),
                slot_index=int(slot.slot_index),
                start_time=slot.start_time.strftime("%H:%M"),
                end_time=slot.end_time.strftime("%H:%M"),
                reason=getattr(sa, "reason", None),
                is_active=bool(sa.is_active),
                created_at=sa.created_at,
            )
        )
    return ListSpecialAllotmentsResponse(entries=out)


@router.post("/special-allotments", response_model=SpecialAllotmentOut)
def upsert_special_allotment(
    payload: UpsertSpecialAllotmentRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    section = get_by_id(db, Section, payload.section_id, tenant_id)
    if section is None:
        raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
    subject = get_by_id(db, Subject, payload.subject_id, tenant_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="SUBJECT_NOT_FOUND")
    teacher = get_by_id(db, Teacher, payload.teacher_id, tenant_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="TEACHER_NOT_FOUND")
    room = get_by_id(db, Room, payload.room_id, tenant_id)
    if room is None:
        raise HTTPException(status_code=404, detail="ROOM_NOT_FOUND")
    if not bool(getattr(room, "is_special", False)):
        raise HTTPException(status_code=400, detail="SPECIAL_ALLOTMENT_ROOM_NOT_SPECIAL")
    slot = get_by_id(db, TimeSlot, payload.slot_id, tenant_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="SLOT_NOT_FOUND")

    allowed_subject_ids = set(
        _required_subject_ids_for_section(db, program_id=section.program_id, section=section, tenant_id=tenant_id)
    )
    if allowed_subject_ids and subject.id not in allowed_subject_ids:
        raise HTTPException(status_code=400, detail="SUBJECT_NOT_ALLOWED_FOR_SECTION")

    # Upsert by (section, slot)
    existing = (
        db.execute(
            where_tenant(
                select(SpecialAllotment)
                .where(SpecialAllotment.section_id == payload.section_id)
                .where(SpecialAllotment.slot_id == payload.slot_id)
                .where(SpecialAllotment.is_active.is_(True)),
                SpecialAllotment,
                tenant_id,
            )
        )
        .scalars()
        .first()
    )

    _validate_special_allotment_refs(
        db,
        section=section,
        subject=subject,
        teacher=teacher,
        room=room,
        slot=slot,
        tenant_id=tenant_id,
        existing_id=existing.id if existing is not None else None,
    )

    if existing is None:
        existing = SpecialAllotment(
            tenant_id=tenant_id,
            section_id=payload.section_id,
            subject_id=payload.subject_id,
            teacher_id=payload.teacher_id,
            room_id=payload.room_id,
            slot_id=payload.slot_id,
            reason=payload.reason,
            is_active=True,
        )
        db.add(existing)
    else:
        existing.subject_id = payload.subject_id
        existing.teacher_id = payload.teacher_id
        existing.room_id = payload.room_id
        existing.reason = payload.reason

    db.commit()

    row = (
        db.execute(
            where_tenant(
                select(SpecialAllotment, Section, Subject, Teacher, Room, TimeSlot)
                .join(Section, Section.id == SpecialAllotment.section_id)
                .join(Subject, Subject.id == SpecialAllotment.subject_id)
                .join(Teacher, Teacher.id == SpecialAllotment.teacher_id)
                .join(Room, Room.id == SpecialAllotment.room_id)
                .join(TimeSlot, TimeSlot.id == SpecialAllotment.slot_id)
                .where(SpecialAllotment.id == existing.id),
                SpecialAllotment,
                tenant_id,
            )
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=500, detail="SPECIAL_ALLOTMENT_WRITE_FAILED")
    sa, sec, subj, teacher, room, slot = row
    return SpecialAllotmentOut(
        id=sa.id,
        section_id=sec.id,
        section_code=sec.code,
        section_name=sec.name,
        subject_id=subj.id,
        subject_code=subj.code,
        subject_name=subj.name,
        subject_type=str(subj.subject_type),
        teacher_id=teacher.id,
        teacher_code=teacher.code,
        teacher_name=teacher.full_name,
        room_id=room.id,
        room_code=room.code,
        room_name=room.name,
        room_type=str(room.room_type),
        slot_id=slot.id,
        day_of_week=int(slot.day_of_week),
        slot_index=int(slot.slot_index),
        start_time=slot.start_time.strftime("%H:%M"),
        end_time=slot.end_time.strftime("%H:%M"),
        reason=getattr(sa, "reason", None),
        is_active=bool(sa.is_active),
        created_at=sa.created_at,
    )


@router.delete("/special-allotments/{entry_id}")
def delete_special_allotment(
    entry_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    row = get_by_id(db, SpecialAllotment, entry_id, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SPECIAL_ALLOTMENT_NOT_FOUND")
    row.is_active = False
    db.commit()
    return {"ok": True}


def _get_academic_year(db: Session, year_number: int, *, tenant_id: uuid.UUID | None) -> AcademicYear:
    q = select(AcademicYear).where(AcademicYear.year_number == int(year_number))
    q = where_tenant(q, AcademicYear, tenant_id)
    ay = db.execute(q).scalar_one_or_none()
    if ay is None:
        raise HTTPException(status_code=404, detail="ACADEMIC_YEAR_NOT_FOUND")
    return ay


@router.get("/time-slots", response_model=ListTimeSlotsResponse)
def list_time_slots(
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    slots = (
        db.execute(where_tenant(select(TimeSlot), TimeSlot, tenant_id).order_by(TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc()))
        .scalars()
        .all()
    )
    return ListTimeSlotsResponse(
        slots=[
            TimeSlotOut(
                id=s.id,
                day_of_week=int(s.day_of_week),
                slot_index=int(s.slot_index),
                start_time=s.start_time.strftime("%H:%M"),
                end_time=s.end_time.strftime("%H:%M"),
                is_lunch_break=bool(getattr(s, "is_lunch_break", False)),
            )
            for s in slots
        ]
    )


@router.get("/runs", response_model=ListRunsResponse)
def list_runs(
    program_code: str | None = Query(default=None),
    academic_year_number: int | None = Query(default=None, ge=1, le=4),
    limit: int = Query(default=50, ge=1, le=200),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    def _normalized_params(run: TimetableRun) -> dict[str, Any]:
        params = dict(run.parameters or {})

        # Backfill scope for legacy runs so UI filters remain stable.
        raw_scope = str(params.get("scope") or params.get("run_scope") or "").strip().upper()
        if raw_scope in {"PROGRAM_GLOBAL", "YEAR_ONLY"}:
            params["scope"] = raw_scope
        else:
            params["scope"] = "YEAR_ONLY" if params.get("academic_year_number") is not None else "PROGRAM_GLOBAL"

        # Backfill solver_type for older runs that only have solver_version metadata.
        if not params.get("solver_type"):
            solver_version = str(run.solver_version or "").strip().upper()
            run_name = str(params.get("run_name") or "").strip().upper()
            if "GA+CP-SAT" in solver_version or "HYBRID" in run_name:
                params["solver_type"] = "HYBRID"
            elif "CP-SAT" in solver_version:
                params["solver_type"] = "CP_SAT_ONLY"

        return params

    q_runs = where_tenant(select(TimetableRun), TimetableRun, tenant_id).order_by(TimetableRun.created_at.desc()).limit(limit)
    rows = db.execute(q_runs).scalars().all()

    runs: list[RunSummary] = []
    for r in rows:
        params = _normalized_params(r)
        if program_code is not None and params.get("program_code") != program_code:
            continue
        if academic_year_number is not None and params.get("academic_year_number") != academic_year_number:
            continue
        runs.append(
            RunSummary(
                id=r.id,
                created_at=r.created_at,
                status=str(r.status),
                solver_version=r.solver_version,
                seed=r.seed,
                parameters=params,
                notes=r.notes,
            )
        )

    return ListRunsResponse(runs=runs)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    run = get_by_id(db, TimetableRun, run_id, tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")

    params = dict(run.parameters or {})
    raw_scope = str(params.get("scope") or params.get("run_scope") or "").strip().upper()
    if raw_scope in {"PROGRAM_GLOBAL", "YEAR_ONLY"}:
        params["scope"] = raw_scope
    else:
        params["scope"] = "YEAR_ONLY" if params.get("academic_year_number") is not None else "PROGRAM_GLOBAL"

    if not params.get("solver_type"):
        solver_version = str(run.solver_version or "").strip().upper()
        run_name = str(params.get("run_name") or "").strip().upper()
        if "GA+CP-SAT" in solver_version or "HYBRID" in run_name:
            params["solver_type"] = "HYBRID"
        elif "CP-SAT" in solver_version:
            params["solver_type"] = "CP_SAT_ONLY"

    q_conflicts_total = where_tenant(
        select(func.count(TimetableConflict.id)).where(TimetableConflict.run_id == run_id),
        TimetableConflict,
        tenant_id,
    )
    conflicts_total = db.execute(q_conflicts_total).scalar_one() or 0

    q_entries_total = where_tenant(
        select(func.count(TimetableEntry.id)).where(TimetableEntry.run_id == run_id),
        TimetableEntry,
        tenant_id,
    )
    entries_total = db.execute(q_entries_total).scalar_one() or 0

    return RunDetail(
        id=run.id,
        created_at=run.created_at,
        status=str(run.status),
        solver_version=run.solver_version,
        seed=run.seed,
        parameters=params,
        notes=run.notes,
        conflicts_total=int(conflicts_total),
        entries_total=int(entries_total),
    )


@router.get("/runs/{run_id}/conflicts", response_model=ListRunConflictsResponse)
def list_run_conflicts(
    run_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    run = get_by_id(db, TimetableRun, run_id, tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")

    q = (
        select(TimetableConflict, Section, Subject, Teacher, Room, TimeSlot)
        .outerjoin(Section, Section.id == TimetableConflict.section_id)
        .outerjoin(Teacher, Teacher.id == TimetableConflict.teacher_id)
        .outerjoin(Subject, Subject.id == TimetableConflict.subject_id)
        .outerjoin(Room, Room.id == TimetableConflict.room_id)
        .outerjoin(TimeSlot, TimeSlot.id == TimetableConflict.slot_id)
        .where(TimetableConflict.run_id == run_id)
        .order_by(TimetableConflict.created_at.asc())
    )
    q = where_tenant(q, TimetableConflict, tenant_id)
    rows = db.execute(q).all()

    return ListRunConflictsResponse(
        run_id=run_id,
        conflicts=[
            (
                lambda c, sec, subj, teacher, room, slot: SolverConflict(
                    id=c.id,
                    severity=str(c.severity),
                    conflict_type=c.conflict_type,
                    message=c.message,
                    section_id=c.section_id,
                    teacher_id=c.teacher_id,
                    subject_id=c.subject_id,
                    room_id=c.room_id,
                    slot_id=c.slot_id,
                    details={
                        **{
                            **(
                                {
                                    "section_code": sec.code,
                                    "section_name": sec.name,
                                }
                                if sec is not None
                                else {}
                            ),
                            **(
                                {
                                    "subject_code": subj.code,
                                    "subject_name": subj.name,
                                    "subject_type": str(subj.subject_type),
                                }
                                if subj is not None
                                else {}
                            ),
                            **(
                                {
                                    "teacher_code": teacher.code,
                                    "teacher_name": teacher.full_name,
                                }
                                if teacher is not None
                                else {}
                            ),
                            **(
                                {
                                    "room_code": room.code,
                                    "room_name": room.name,
                                    "room_type": str(room.room_type),
                                }
                                if room is not None
                                else {}
                            ),
                            **(
                                {
                                    "day_of_week": slot.day_of_week,
                                    "slot_index": slot.slot_index,
                                    "start_time": slot.start_time.isoformat(),
                                    "end_time": slot.end_time.isoformat(),
                                }
                                if slot is not None
                                else {}
                            ),
                        },
                        **(c.details_json or c.metadata_json or {}),
                    },
                    metadata=c.metadata_json or {},
                )
            )(*row)
            for row in rows
        ],
    )


@router.get("/runs/{run_id}/entries", response_model=ListRunEntriesResponse)
def list_run_entries(
    run_id: uuid.UUID,
    section_code: str | None = Query(default=None),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    run = get_by_id(db, TimetableRun, run_id, tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")

    section_id_filter: uuid.UUID | None = None
    if section_code is not None:
        params = run.parameters or {}
        program_code = params.get("program_code")
        academic_year_number = params.get("academic_year_number")
        if not program_code:
            raise HTTPException(status_code=422, detail="RUN_MISSING_PARAMETERS")
        q_program = where_tenant(select(Program).where(Program.code == program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            raise HTTPException(status_code=422, detail="RUN_PROGRAM_NOT_FOUND")

        year_id: uuid.UUID | None = None
        if academic_year_number is not None:
            year_id = _get_academic_year(db, int(academic_year_number), tenant_id=tenant_id).id
        elif getattr(run, "academic_year_id", None) is not None:
            year_id = run.academic_year_id

        q_section = select(Section).where(Section.program_id == program.id).where(Section.code == section_code)
        if year_id is not None:
            q_section = q_section.where(Section.academic_year_id == year_id)
        q_section = where_tenant(q_section, Section, tenant_id)
        section = db.execute(q_section.order_by(Section.created_at.desc())).scalars().first()
        if section is None:
            raise HTTPException(status_code=404, detail="SECTION_NOT_FOUND")
        section_id_filter = section.id

    q = (
        select(TimetableEntry, Section, Subject, Teacher, Room, TimeSlot, ElectiveBlock)
        .join(Section, Section.id == TimetableEntry.section_id)
        .join(Subject, Subject.id == TimetableEntry.subject_id)
        .join(Teacher, Teacher.id == TimetableEntry.teacher_id)
        .join(Room, Room.id == TimetableEntry.room_id)
        .join(TimeSlot, TimeSlot.id == TimetableEntry.slot_id)
        .outerjoin(ElectiveBlock, ElectiveBlock.id == TimetableEntry.elective_block_id)
        .where(TimetableEntry.run_id == run_id)
    )
    q = where_tenant(q, TimetableEntry, tenant_id)
    if section_id_filter is not None:
        q = q.where(TimetableEntry.section_id == section_id_filter)

    q = q.order_by(Section.code.asc(), TimeSlot.day_of_week.asc(), TimeSlot.slot_index.asc())

    rows = db.execute(q).all()
    entries: list[TimetableEntryOut] = []
    for te, sec, subj, teacher, room, slot, eb in rows:
        entries.append(
            TimetableEntryOut(
                id=te.id,
                run_id=te.run_id,
                section_id=sec.id,
                section_code=sec.code,
                section_name=sec.name,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                subject_type=str(subj.subject_type),
                teacher_id=teacher.id,
                teacher_code=teacher.code,
                teacher_name=teacher.full_name,
                room_id=room.id,
                room_code=room.code,
                room_name=room.name,
                room_type=str(room.room_type),
                slot_id=slot.id,
                day_of_week=int(slot.day_of_week),
                slot_index=int(slot.slot_index),
                start_time=slot.start_time.strftime("%H:%M"),
                end_time=slot.end_time.strftime("%H:%M"),
                combined_class_id=te.combined_class_id,
                elective_block_id=getattr(te, "elective_block_id", None),
                elective_block_name=(eb.name if eb is not None else None),
                created_at=te.created_at,
            )
        )

    return ListRunEntriesResponse(run_id=run_id, entries=entries)


@router.post("/generate", response_model=GenerateTimetableResponse)
def generate_timetable(
    payload: GenerateTimetableRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    try:
        # Explicit connectivity validation before creating any rows.
        validate_db_connection(db)

        ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

        run = TimetableRun(
            tenant_id=tenant_id,
            academic_year_id=ay.id,
            seed=payload.seed,
            status="CREATED",
            parameters={
                "program_code": payload.program_code,
                "academic_year_number": payload.academic_year_number,
                "scope": "ACADEMIC_YEAR",
                **({"tenant_id": str(tenant_id)} if tenant_id is not None else {}),
            },
        )
        db.add(run)
        db.flush()  # assign run.id

        program = db.execute(where_tenant(select(Program).where(Program.code == payload.program_code), Program, tenant_id)).scalar_one_or_none()
        if program is None:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="PROGRAM_NOT_FOUND",
                        message=f"Unknown program_code '{payload.program_code}'.",
                    )
                ],
            )

        q_sections = (
            select(Section)
            .where(Section.program_id == program.id)
            .where(Section.academic_year_id == ay.id)
            .where(Section.is_active.is_(True))
        )
        q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
        sections = db.execute(q_sections).scalars().all()
        if not sections:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="NO_ACTIVE_SECTIONS",
                        message=f"No active sections found for program '{payload.program_code}' year {payload.academic_year_number}.",
                    )
                ],
            )

        conflicts = validate_prereqs(
            db,
            run=run,
            program_id=program.id,
            academic_year_id=ay.id,
            sections=sections,
        )
        errors = [c for c in conflicts if str(c.severity).upper() != "WARN"]
        warnings = [c for c in conflicts if str(c.severity).upper() == "WARN"]
        if errors:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        severity=c.severity,
                        conflict_type=c.conflict_type,
                        message=c.message,
                        section_id=c.section_id,
                        teacher_id=c.teacher_id,
                        subject_id=c.subject_id,
                        room_id=c.room_id,
                        slot_id=c.slot_id,
                        details=(c.metadata or {}),
                        metadata=(c.metadata or {}),
                    )
                    for c in conflicts
                ],
            )

        # Validations passed; actual solve will happen in the next phase.
        run.status = "CREATED"
        db.commit()
        return GenerateTimetableResponse(
            run_id=run.id,
            status="READY_FOR_SOLVE",
            conflicts=[
                SolverConflict(
                    severity=c.severity,
                    conflict_type=c.conflict_type,
                    message=c.message,
                    section_id=c.section_id,
                    teacher_id=c.teacher_id,
                    subject_id=c.subject_id,
                    room_id=c.room_id,
                    slot_id=c.slot_id,
                    metadata=(c.metadata or {}),
                )
                for c in warnings
            ],
        )

    except DatabaseUnavailableError:
        db.rollback()
        raise
    except SAOperationalError as exc:
        db.rollback()
        if is_transient_db_connectivity_error(exc):
            raise DatabaseUnavailableError("Database temporarily unavailable") from exc
        raise


@router.post("/validate", response_model=ValidateTimetableResponse)
def validate_timetable(
    payload: ValidateTimetableRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    """Validate solver feasibility without running the solver.

    Runs prerequisite checks (missing config, broken references, time windows)
    plus capacity analysis (teacher overload, room shortage, section slot deficit,
    combined-group domain collapse, subject-specific room conflicts).

    Returns structured errors/warnings/capacity_issues in under 1 second for
    typical datasets.  Does NOT create a persistent run or modify the database.
    """
    try:
        validate_db_connection(db)

        # ── 1. Resolve program ────────────────────────────────────────────
        q_program = where_tenant(
            select(Program).where(Program.code == payload.program_code), Program, tenant_id
        )
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            return ValidateTimetableResponse(
                status="INVALID",
                errors=[
                    SolverConflict(
                        conflict_type="PROGRAM_NOT_FOUND",
                        message=f"Unknown program_code '{payload.program_code}'.",
                    )
                ],
            )

        # ── 2. Load sections ──────────────────────────────────────────────
        academic_year_id = None
        q_sections = (
            select(Section)
            .where(Section.program_id == program.id)
            .where(Section.is_active.is_(True))
        )
        if payload.academic_year_number is not None:
            q_ay = where_tenant(
                select(AcademicYear)
                .where(AcademicYear.year_number == payload.academic_year_number),
                AcademicYear,
                tenant_id,
            )
            ay = db.execute(q_ay).scalar_one_or_none()
            if ay is not None:
                academic_year_id = ay.id
                q_sections = q_sections.where(Section.academic_year_id == academic_year_id)

        q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
        sections = db.execute(q_sections).scalars().all()

        if not sections:
            return ValidateTimetableResponse(
                status="INVALID",
                errors=[
                    SolverConflict(
                        conflict_type="NO_ACTIVE_SECTIONS",
                        message=f"No active sections found for program '{payload.program_code}'.",
                    )
                ],
            )

        # ── 3. Prerequisite validation (read-only, transient run object) ──
        # validate_prereqs needs a run object only to query SectionBreak
        # (which returns no rows for a fresh UUID) and to read tenant_id.
        class _TransientRun:
            def __init__(self, tid: uuid.UUID | None) -> None:
                self.id = uuid.uuid4()
                self.tenant_id = tid

        transient_run = _TransientRun(tenant_id)

        prereq_conflicts = validate_prereqs(
            db,
            run=transient_run,
            program_id=program.id,
            academic_year_id=academic_year_id,
            sections=sections,
        )
        errors = [c for c in prereq_conflicts if str(c.severity).upper() == "ERROR"]
        warn_from_prereqs = [c for c in prereq_conflicts if str(c.severity).upper() == "WARN"]

        # ── 4. Capacity analysis ──────────────────────────────────────────
        cap_data = build_capacity_data(
            db,
            program_id=program.id,
            academic_year_id=academic_year_id,
            sections=list(sections),
            tenant_id=tenant_id,
        )
        cap_result = analyze_capacity(cap_data)

        # ── 5. Convert capacity issues to ValidationIssue objects ─────────
        capacity_issues: list[ValidationIssue] = []
        for issue in cap_result.get("issues", []):
            t = str(issue.get("type", ""))
            rt = str(issue.get("resource_type", ""))
            req = int(issue.get("required_slots", 0) or 0)
            avail = int(issue.get("available_slots", 0) or 0)
            short = int(issue.get("shortage", 0) or 0)
            resource = str(issue.get("resource", "") or "")

            if rt == "TEACHER":
                teacher_name = resource.replace("Teacher ", "")
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    teacher_id=issue.get("teacher_id"),
                    teacher=teacher_name,
                    required=req,
                    capacity=avail,
                    shortage=short,
                    contributors=issue.get("contributors", []),
                    suggestion=(
                        f"Increase max_per_day or max_per_week for {teacher_name} "
                        f"by at least {short} session(s)."
                    ),
                )
            elif rt == "ROOM_TYPE":
                room_type_name = resource.replace("RoomType ", "")
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    required=req,
                    capacity=avail,
                    shortage=short,
                    suggestion=(
                        f"Add more {room_type_name} rooms or reduce scheduled sessions "
                        f"by {short} slot(s)."
                    ),
                )
            elif rt == "SECTION":
                section_name = resource.replace("Section ", "")
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    section_id=issue.get("section_id"),
                    section=section_name,
                    required=req,
                    capacity=avail,
                    shortage=short,
                    suggestion=(
                        f"Expand the time window for section {section_name} "
                        f"or reduce sessions by {short} slot(s)."
                    ),
                )
            elif rt == "COMBINED_GROUP":
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    subject_id=issue.get("subject_id"),
                    required=req,
                    capacity=avail,
                    shortage=short,
                    suggestion=(
                        "Ensure the intersection of the combined sections' time windows "
                        f"has at least {req} free slots (currently {avail})."
                    ),
                )
            elif rt == "SUBJECT_ROOM":
                subj_name = resource.replace("Subject ", "")
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    subject_id=issue.get("subject_id"),
                    subject=subj_name,
                    required=req,
                    capacity=avail,
                    shortage=short,
                    suggestion=(
                        f"Add more allowed rooms for {subj_name} or remove the room "
                        "restriction to use the default pool."
                    ),
                )
            else:
                vi = ValidationIssue(
                    type=t,
                    resource=resource,
                    resource_type=rt,
                    required=req,
                    capacity=avail,
                    shortage=short,
                )
            capacity_issues.append(vi)

        # ── 6. Build response ─────────────────────────────────────────────
        def _to_conflict(c) -> SolverConflict:
            return SolverConflict(
                severity=c.severity,
                conflict_type=c.conflict_type,
                message=c.message,
                section_id=c.section_id,
                teacher_id=c.teacher_id,
                subject_id=c.subject_id,
                room_id=c.room_id,
                slot_id=c.slot_id,
                details=(c.metadata or {}),
                metadata=(c.metadata or {}),
            )

        error_conflicts = [_to_conflict(c) for c in errors]
        warning_conflicts = [_to_conflict(c) for c in warn_from_prereqs]

        has_block = bool(error_conflicts) or bool(capacity_issues)
        has_warn = bool(warning_conflicts)

        if has_block:
            status = "INVALID"
        elif has_warn:
            status = "WARNINGS"
        else:
            status = "VALID"

        return ValidateTimetableResponse(
            status=status,
            errors=error_conflicts,
            warnings=warning_conflicts,
            capacity_issues=capacity_issues,
            summary=cap_result.get("summary", {}),
        )

    except DatabaseUnavailableError:
        raise
    except SAOperationalError as exc:
        if is_transient_db_connectivity_error(exc):
            raise DatabaseUnavailableError("Database temporarily unavailable") from exc
        raise
    except Exception as exc:
        logger.exception(
            "solver.validate_failed tenant_id=%s program_code=%s academic_year_number=%s error=%s",
            str(tenant_id) if tenant_id is not None else "shared",
            str(payload.program_code),
            str(payload.academic_year_number),
            str(exc),
        )
        return ValidateTimetableResponse(
            status="INVALID",
            errors=[
                SolverConflict(
                    severity="ERROR",
                    conflict_type="VALIDATION_RUNTIME_ERROR",
                    message="Validation failed due to an internal error.",
                    metadata={
                        "error": str(exc),
                    },
                )
            ],
            warnings=[],
            capacity_issues=[
                ValidationIssue(
                    type="validation_failed",
                    resource_type="SYSTEM",
                    resource="solver.validate",
                    suggestion="Check backend logs for stack trace and fix data/model mismatches.",
                )
            ],
            summary={},
        )


@router.post("/generate-global", response_model=GenerateTimetableResponse)
def generate_timetable_global(
    payload: GenerateGlobalTimetableRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    """Program-wide generate endpoint.

    Creates a run and performs validations for a full-program solve (all years).
    """
    try:
        validate_db_connection(db)

        run = TimetableRun(
            tenant_id=tenant_id,
            academic_year_id=None,
            seed=payload.seed,
            status="CREATED",
            parameters={
                "program_code": payload.program_code,
                "scope": "PROGRAM_GLOBAL",
                **({"tenant_id": str(tenant_id)} if tenant_id is not None else {}),
            },
        )
        db.add(run)
        db.flush()

        q_program = where_tenant(select(Program).where(Program.code == payload.program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="PROGRAM_NOT_FOUND",
                        message=f"Unknown program_code '{payload.program_code}'.",
                    )
                ],
            )

        q_sections = select(Section).where(Section.program_id == program.id).where(Section.is_active.is_(True))
        q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
        sections = db.execute(q_sections).scalars().all()
        if not sections:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="NO_ACTIVE_SECTIONS",
                        message=f"No active sections found for program '{payload.program_code}'.",
                    )
                ],
            )

        run.parameters = {
            **(run.parameters or {}),
            "academic_year_ids": sorted({str(s.academic_year_id) for s in sections}),
        }

        conflicts = validate_prereqs(
            db,
            run=run,
            program_id=program.id,
            academic_year_id=None,
            sections=sections,
        )
        errors = [c for c in conflicts if str(c.severity).upper() != "WARN"]
        warnings = [c for c in conflicts if str(c.severity).upper() == "WARN"]
        if errors:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return GenerateTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        severity=c.severity,
                        conflict_type=c.conflict_type,
                        message=c.message,
                        section_id=c.section_id,
                        teacher_id=c.teacher_id,
                        subject_id=c.subject_id,
                        room_id=c.room_id,
                        slot_id=c.slot_id,
                        details=(c.metadata or {}),
                        metadata=(c.metadata or {}),
                    )
                    for c in conflicts
                ],
            )

        run.status = "CREATED"
        db.commit()
        return GenerateTimetableResponse(
            run_id=run.id,
            status="READY_FOR_SOLVE",
            conflicts=[
                SolverConflict(
                    severity=c.severity,
                    conflict_type=c.conflict_type,
                    message=c.message,
                    section_id=c.section_id,
                    teacher_id=c.teacher_id,
                    subject_id=c.subject_id,
                    room_id=c.room_id,
                    slot_id=c.slot_id,
                    metadata=(c.metadata or {}),
                )
                for c in warnings
            ],
        )

    except DatabaseUnavailableError:
        db.rollback()
        raise
    except SAOperationalError as exc:
        db.rollback()
        if is_transient_db_connectivity_error(exc):
            raise DatabaseUnavailableError("Database temporarily unavailable") from exc
        raise


@router.post("/solve", response_model=SolveTimetableResponse)
def solve_timetable(
    payload: SolveTimetableRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    run: TimetableRun | None = None
    try:
        max_time_seconds = float(payload.max_time_seconds)
        if settings.environment.lower() == "production":
            # Enforce 5-minute ceiling in production; callers may request less.
            max_time_seconds = min(max_time_seconds, 300.0)

        # Explicit connectivity validation before creating any rows.
        validate_db_connection(db)

        ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

        run = TimetableRun(
            tenant_id=tenant_id,
            academic_year_id=ay.id,
            seed=payload.seed,
            status="CREATED",
            parameters={
                "program_code": payload.program_code,
                "academic_year_number": payload.academic_year_number,
                "max_time_seconds": max_time_seconds,
                "relax_teacher_load_limits": payload.relax_teacher_load_limits,
                "require_optimal": payload.require_optimal,
                "scope": "ACADEMIC_YEAR",
                **({"tenant_id": str(tenant_id)} if tenant_id is not None else {}),
            },
        )
        db.add(run)
        db.flush()
        # Ensure we have a persistent run_id even if the solve crashes later.
        db.commit()

        q_program = where_tenant(select(Program).where(Program.code == payload.program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return SolveTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="PROGRAM_NOT_FOUND",
                        message=f"Unknown program_code '{payload.program_code}'.",
                    )
                ],
            )

        q_sections = (
            select(Section)
            .where(Section.program_id == program.id)
            .where(Section.academic_year_id == ay.id)
            .where(Section.is_active.is_(True))
        )
        q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
        sections = db.execute(q_sections).scalars().all()
        if not sections:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return SolveTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        conflict_type="NO_ACTIVE_SECTIONS",
                        message=f"No active sections found for program '{payload.program_code}' year {payload.academic_year_number}.",
                    )
                ],
            )

        conflicts = validate_prereqs(
            db,
            run=run,
            program_id=program.id,
            academic_year_id=ay.id,
            sections=sections,
        )
        errors = [c for c in conflicts if str(c.severity).upper() != "WARN"]
        warnings = [c for c in conflicts if str(c.severity).upper() == "WARN"]
        if errors:
            run.status = "VALIDATION_FAILED"
            db.commit()
            return SolveTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                conflicts=[
                    SolverConflict(
                        severity=c.severity,
                        conflict_type=c.conflict_type,
                        message=c.message,
                        section_id=c.section_id,
                        teacher_id=c.teacher_id,
                        subject_id=c.subject_id,
                        room_id=c.room_id,
                        slot_id=c.slot_id,
                        details=(c.metadata or {}),
                        metadata=(c.metadata or {}),
                    )
                    for c in conflicts
                ],
            )

        # Pre-solve capacity analysis and bottleneck reporting
        # Capacity analysis is diagnostic + early validation, but it should not be allowed to hard-crash solving.
        cap: dict = {"issues": [], "debug": False, "summary": {}, "minimal_relaxation": []}
        capacity_diagnostics: list[dict] = []
        try:
            cap_data = build_capacity_data(
                db,
                program_id=program.id,
                academic_year_id=ay.id,
                sections=sections,
                tenant_id=tenant_id,
            )
            cap = analyze_capacity(cap_data, debug=getattr(payload, "debug_capacity_mode", False))
            capacity_diagnostics = (
                ([{"type": "CAPACITY_SUMMARY", "data": cap.get("summary", {})}] if cap.get("debug") else [])
            )
        except Exception as e:
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="CAPACITY_ANALYSIS_FAILED",
                    message=(
                        "Capacity analysis failed; continuing solve. "
                        + f"{type(e).__name__}: {str(e)[:500]}"
                    ),
                    metadata_json={"error_type": type(e).__name__},
                )
            )
            db.flush()

        issue_types = {i.get("type") for i in cap.get("issues", [])}
        only_teacher_overload = bool(issue_types) and issue_types.issubset({"CAPACITY_OVERLOAD"})

        if cap.get("issues") and not payload.relax_teacher_load_limits and not (
            getattr(payload, "smart_relaxation", False) and only_teacher_overload
        ):
            run.status = "VALIDATION_FAILED"
            db.commit()
            return SolveTimetableResponse(
                run_id=run.id,
                status="FAILED_VALIDATION",
                reason_summary=f"Capacity analysis found {len(cap['issues'])} blocking shortages",
                diagnostics=capacity_diagnostics,
                minimal_relaxation=cap.get("minimal_relaxation", []),
                conflicts=[
                    SolverConflict(
                        severity="ERROR",
                        conflict_type=i.get("type") or "CAPACITY",
                        message=f"{i.get('resource')}: required={i.get('required_slots')} available={i.get('available_slots')} shortage={i.get('shortage')}",
                        details={k: v for k, v in i.items() if k not in {"type", "required_slots", "available_slots", "shortage", "resource"}},
                        metadata={k: v for k, v in i.items() if k not in {"type"}},
                    )
                    for i in cap.get("issues", [])
                ],
            )

        # Smart relaxation: auto-relax teacher loads when only teacher capacity issues
        auto_relaxed = False
        if getattr(payload, "smart_relaxation", False) and only_teacher_overload:
            payload.relax_teacher_load_limits = True
            auto_relaxed = True
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="SMART_RELAXATION_APPLIED",
                    message="Auto-relaxed teacher load limits to address capacity shortages.",
                    metadata_json={"minimal_relaxation": cap.get("minimal_relaxation", [])},
                )
            )
            db.flush()

        if payload.relax_teacher_load_limits:
            # Persist an explicit warning so runs are auditable.
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="RELAXED_TEACHER_LOAD_LIMITS",
                    message="Solved with teacher load limits disabled (max_per_day/max_per_week not enforced).",
                    metadata_json={},
                )
            )
            db.flush()

        result = solve_program_year(
            db,
            run=run,
            program_id=program.id,
            academic_year_id=ay.id,
            seed=payload.seed,
            max_time_seconds=max_time_seconds,
            enforce_teacher_load_limits=not payload.relax_teacher_load_limits,
            require_optimal=payload.require_optimal,
            allow_extended_solve=getattr(payload, "allow_extended_solve", False),
        )

        # Soft conflicts (warnings) created during solve (e.g., room assignment conflicts).
        soft_conflicts: list[TimetableConflict] = []
        if str(result.status) in {"FEASIBLE", "OPTIMAL"}:
            q_soft = (
                select(TimetableConflict)
                .where(TimetableConflict.run_id == run.id)
                .where(TimetableConflict.severity == "WARN")
                .order_by(TimetableConflict.created_at.asc())
                .limit(200)
            )
            q_soft = where_tenant(q_soft, TimetableConflict, tenant_id)
            soft_conflicts = db.execute(q_soft).scalars().all()

        return SolveTimetableResponse(
            run_id=run.id,
            status=result.status,
            entries_written=result.entries_written,
            reason_summary=getattr(result, "reason_summary", None),
            diagnostics=(capacity_diagnostics + (getattr(result, "diagnostics", []) or [])),
            objective_score=getattr(result, "objective_score", None),
            improvements_possible=True
            if str(result.status) in {"FEASIBLE", "SUBOPTIMAL"}
            else (False if str(result.status) == "OPTIMAL" else None),
            warnings=getattr(result, "warnings", []) or [],
            solver_stats=getattr(result, "solver_stats", {}) or {},
            best_bound=getattr(result, "best_objective_bound", None),
            optimality_gap=getattr(result, "optimality_gap", None),
            solve_time_seconds=getattr(result, "solve_time_seconds", None),
            message=getattr(result, "message", None),
            conflicts=(
                [
                    SolverConflict(
                        severity=c.severity,
                        conflict_type=c.conflict_type,
                        message=c.message,
                        section_id=c.section_id,
                        teacher_id=c.teacher_id,
                        subject_id=c.subject_id,
                        room_id=c.room_id,
                        slot_id=c.slot_id,
                        details=(c.metadata or {}),
                        metadata=(c.metadata or {}),
                    )
                    for c in warnings
                ]
                + [
                    SolverConflict(
                        id=c.id,
                        severity=c.severity,
                        conflict_type=c.conflict_type,
                        message=c.message,
                        section_id=c.section_id,
                        teacher_id=c.teacher_id,
                        subject_id=c.subject_id,
                        room_id=c.room_id,
                        slot_id=c.slot_id,
                        details=(c.details_json or c.metadata_json or {}),
                        metadata=c.metadata_json or {},
                    )
                    for c in result.conflicts
                ]
            ),
            soft_conflicts=[
                SolverConflict(
                    id=c.id,
                    severity=c.severity,
                    conflict_type=c.conflict_type,
                    message=c.message,
                    section_id=c.section_id,
                    teacher_id=c.teacher_id,
                    subject_id=c.subject_id,
                    room_id=c.room_id,
                    slot_id=c.slot_id,
                    details=(c.details_json or c.metadata_json or {}),
                    metadata=(c.metadata_json or c.details_json or {}),
                )
                for c in soft_conflicts
            ],
        )

    except DatabaseUnavailableError:
        db.rollback()
        raise
    except SAOperationalError as exc:
        db.rollback()
        if is_transient_db_connectivity_error(exc):
            raise DatabaseUnavailableError("Database temporarily unavailable") from exc
        raise
    except SolverInvariantError as exc:
        try:
            db.rollback()
        except Exception:
            pass
        run_id = (run.id if run is not None else uuid.uuid4())
        if run is not None:
            try:
                run.status = "ERROR"
                run.notes = (f"SolverInvariantError({exc.code}): {str(exc)}")[:500]
                db.add(run)
                db.commit()
            except Exception:
                pass
        raise HTTPException(
            status_code=500,
            detail={
                "error": "SOLVER_INTEGRITY_ERROR",
                "type": str(exc.code),
                "message": str(exc),
                "run_id": str(run_id),
                "details": getattr(exc, "details", {}) or {},
            },
        )
    except IntegrityError as exc:
        try:
            db.rollback()
        except Exception:
            pass
        run_id = (run.id if run is not None else uuid.uuid4())
        if run is not None:
            try:
                run.status = "ERROR"
                run.notes = (f"IntegrityError: {str(exc.orig) if getattr(exc, 'orig', None) else str(exc)}")[:500]
                db.add(run)
                db.commit()
            except Exception:
                pass
        raise HTTPException(
            status_code=500,
            detail={
                "error": "SOLVER_DB_INTEGRITY_ERROR",
                "message": "Database integrity constraint violated while saving solver results.",
                "run_id": str(run_id),
            },
        )
    except Exception as exc:
        # Prefer returning a structured response (frontend can display run_id) over a raw 500.
        try:
            db.rollback()
        except Exception:
            pass
        notes: str | None = None
        if run is not None:
            try:
                run.status = "ERROR"
                run.notes = (f"{type(exc).__name__}: {str(exc)}")[:500]
                notes = run.notes
                db.add(run)
                db.commit()
            except Exception:
                pass
        logger.exception("/api/solver/solve crashed")
        return SolveTimetableResponse(
            run_id=(run.id if run is not None else uuid.uuid4()),
            status="ERROR",
            entries_written=0,
            conflicts=[
                SolverConflict(
                    severity="ERROR",
                    conflict_type="INTERNAL_ERROR",
                    message="Internal error while solving.",
                    details={
                        "run_id": str(run.id) if run is not None else None,
                        "error": notes,
                    },
                    metadata={"run_id": str(run.id) if run is not None else None},
                )
            ],
        )


def _global_solve_body(
    run_id: uuid.UUID,
    payload: SolveGlobalTimetableRequest,
    tenant_id: uuid.UUID | None,
    max_time_seconds: float,
) -> None:
    """Execute the full validation + solve in a background thread (own DB session).

    Results are written to DB so the caller can poll GET /api/solver/runs/{run_id}.
    """
    db = SessionLocal()
    try:
        run = db.get(TimetableRun, run_id)
        if run is None:
            logger.error("_global_solve_body: run %s not found", run_id)
            return

        q_program = where_tenant(select(Program).where(Program.code == payload.program_code), Program, tenant_id)
        program = db.execute(q_program).scalar_one_or_none()
        if program is None:
            run.status = "VALIDATION_FAILED"
            run.parameters = {**(run.parameters or {}), "run_status": "FAILED"}
            run.notes = f"Unknown program_code {payload.program_code!r}."
            db.commit()
            return

        selected_ay = None
        selected_ay_id = None
        if payload.academic_year_number is not None:
            selected_ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)
            selected_ay_id = selected_ay.id

        q_sections = (
            select(Section)
            .where(Section.program_id == program.id)
            .where(Section.is_active.is_(True))
        )
        if selected_ay_id is not None:
            q_sections = q_sections.where(Section.academic_year_id == selected_ay_id)
        q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
        sections = db.execute(q_sections).scalars().all()
        if not sections:
            run.status = "VALIDATION_FAILED"
            run.parameters = {**(run.parameters or {}), "run_status": "FAILED"}
            if payload.academic_year_number is not None:
                run.notes = (
                    f"No active sections for program {payload.program_code!r} "
                    f"year {int(payload.academic_year_number)}."
                )
            else:
                run.notes = f"No active sections for program {payload.program_code!r}."
            db.commit()
            return

        run.parameters = {
            **(run.parameters or {}),
            "academic_year_ids": sorted({str(s.academic_year_id) for s in sections}),
            **({"academic_year_number": int(payload.academic_year_number)} if payload.academic_year_number is not None else {}),
        }

        prereq_conflicts = validate_prereqs(
            db,
            run=run,
            program_id=program.id,
            academic_year_id=selected_ay_id,
            sections=sections,
        )
        errors = [c for c in prereq_conflicts if str(c.severity).upper() != "WARN"]
        if errors:
            run.status = "VALIDATION_FAILED"
            run.parameters = {**(run.parameters or {}), "run_status": "FAILED"}
            db.commit()
            return

        cap: dict = {"issues": [], "debug": False, "summary": {}, "minimal_relaxation": []}
        capacity_diagnostics: list = []
        try:
            cap_data = build_capacity_data(
                db,
                program_id=program.id,
                academic_year_id=selected_ay_id,
                sections=sections,
                tenant_id=tenant_id,
            )
            cap = analyze_capacity(cap_data, debug=getattr(payload, "debug_capacity_mode", False))
            capacity_diagnostics = (
                [{"type": "CAPACITY_SUMMARY", "data": cap.get("summary", {})}] if cap.get("debug") else []
            )
        except Exception as e:
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="CAPACITY_ANALYSIS_FAILED",
                    message=(
                        "Capacity analysis failed; continuing solve. "
                        + f"{type(e).__name__}: {str(e)[:500]}"
                    ),
                    metadata_json={"error_type": type(e).__name__},
                )
            )
            db.flush()

        issue_types = {i.get("type") for i in cap.get("issues", [])}
        only_teacher_overload = bool(issue_types) and issue_types.issubset({"CAPACITY_OVERLOAD"})

        if cap.get("issues") and not payload.relax_teacher_load_limits and not (
            getattr(payload, "smart_relaxation", False) and only_teacher_overload
        ):
            run.status = "VALIDATION_FAILED"
            run.parameters = {**(run.parameters or {}), "run_status": "FAILED"}
            run.notes = f"Capacity analysis found {len(cap['issues'])} blocking shortages"
            db.commit()
            return

        if getattr(payload, "smart_relaxation", False) and only_teacher_overload:
            payload.relax_teacher_load_limits = True
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="SMART_RELAXATION_APPLIED",
                    message="Auto-relaxed teacher load limits to address capacity shortages.",
                    metadata_json={"minimal_relaxation": cap.get("minimal_relaxation", [])},
                )
            )
            db.flush()

        if payload.relax_teacher_load_limits:
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="RELAXED_TEACHER_LOAD_LIMITS",
                    message="Solved with teacher load limits disabled.",
                    metadata_json={},
                )
            )
            db.flush()

        solver_type = str(getattr(payload, "solver_type", "HYBRID") or "HYBRID").upper()
        if solver_type in {"GA_ONLY", "HYBRID"}:
            cfg_keys = {
                "population_size",
                "generations",
                "crossover_rate",
                "mutation_rate",
                "elitism_count",
                "stagnation_window",
                "mutation_boost",
                "target_fitness",
                "max_score",
                "tournament_k",
                "cp_sat_max_time_seconds",
            }
            cfg_overrides = {
                k: v
                for k, v in payload.model_dump(exclude_none=True).items()
                if k in cfg_keys
            }
            if "cp_sat_max_time_seconds" not in cfg_overrides:
                cfg_overrides["cp_sat_max_time_seconds"] = min(float(max_time_seconds), 5.0)
            try:
                run_and_persist_dual_solver(
                    db,
                    run=run,
                    program_id=program.id,
                    tenant_id=tenant_id,
                    solver_type=solver_type,
                    academic_year_id=selected_ay_id,
                    config_overrides=cfg_overrides,
                )

                # Merge additional diagnostics for run polling consumers.
                run.parameters = {
                    **(run.parameters or {}),
                    "_solver_result": {
                        **((run.parameters or {}).get("_solver_result") or {}),
                        "diagnostics": capacity_diagnostics,
                        "message": "Dual-solver run completed",
                    },
                }
                db.commit()
            except RuntimeError as exc:
                if "initial feasible population" not in str(exc).lower():
                    raise

                logger.warning(
                    "hybrid_init_failed_fallback_to_cpsat run_id=%s tenant_id=%s program_code=%s year=%s error=%s",
                    str(run.id),
                    str(tenant_id) if tenant_id is not None else "shared",
                    str(payload.program_code),
                    str(payload.academic_year_number) if payload.academic_year_number is not None else "all",
                    str(exc),
                )

                if selected_ay_id is not None:
                    result = solve_program_year(
                        db,
                        run=run,
                        program_id=program.id,
                        academic_year_id=selected_ay_id,
                        seed=payload.seed,
                        max_time_seconds=max_time_seconds,
                        enforce_teacher_load_limits=not payload.relax_teacher_load_limits,
                        require_optimal=payload.require_optimal,
                        allow_extended_solve=getattr(payload, "allow_extended_solve", False),
                    )
                else:
                    result = solve_program_global(
                        db,
                        run=run,
                        program_id=program.id,
                        seed=payload.seed,
                        max_time_seconds=max_time_seconds,
                        enforce_teacher_load_limits=not payload.relax_teacher_load_limits,
                        require_optimal=payload.require_optimal,
                        allow_extended_solve=getattr(payload, "allow_extended_solve", False),
                    )

                hard_ok = str(result.status) in {"OPTIMAL", "FEASIBLE", "SUBOPTIMAL"}
                run.parameters = {
                    **(run.parameters or {}),
                    "run_status": "COMPLETED" if hard_ok else "FAILED",
                    "run_name": "CP-SAT (fallback)",
                    "solver_type": "CP_SAT_ONLY",
                    "_solver_result": {
                        "run_name": "CP-SAT (fallback)",
                        "solver_type": "CP_SAT_ONLY",
                        "run_status": "COMPLETED" if hard_ok else "FAILED",
                        "objective_score": getattr(result, "objective_score", None),
                        "solver_stats": getattr(result, "solver_stats", {}) or {},
                        "best_bound": getattr(result, "best_objective_bound", None),
                        "optimality_gap": getattr(result, "optimality_gap", None),
                        "solve_time_seconds": getattr(result, "solve_time_seconds", None),
                        "entries_written": result.entries_written,
                        "hard_constraints_satisfied": hard_ok,
                        "cp_sat_repair_applied": False,
                        "reason_summary": "Hybrid initialization failed; fell back to CP-SAT.",
                        "warnings": (getattr(result, "warnings", []) or []) + [
                            "Hybrid initialization failed; CP-SAT fallback used."
                        ],
                        "diagnostics": capacity_diagnostics,
                        "message": getattr(result, "message", None),
                        "fallback_from": solver_type,
                    },
                }
                db.commit()
        else:
            if selected_ay_id is not None:
                result = solve_program_year(
                    db,
                    run=run,
                    program_id=program.id,
                    academic_year_id=selected_ay_id,
                    seed=payload.seed,
                    max_time_seconds=max_time_seconds,
                    enforce_teacher_load_limits=not payload.relax_teacher_load_limits,
                    require_optimal=payload.require_optimal,
                    allow_extended_solve=getattr(payload, "allow_extended_solve", False),
                )
            else:
                result = solve_program_global(
                    db,
                    run=run,
                    program_id=program.id,
                    seed=payload.seed,
                    max_time_seconds=max_time_seconds,
                    enforce_teacher_load_limits=not payload.relax_teacher_load_limits,
                    require_optimal=payload.require_optimal,
                    allow_extended_solve=getattr(payload, "allow_extended_solve", False),
                )

            # Persist solver stats so the polling endpoint can surface them.
            try:
                hard_ok = str(result.status) in {"OPTIMAL", "FEASIBLE", "SUBOPTIMAL"}
                run.parameters = {
                    **(run.parameters or {}),
                    "run_status": "COMPLETED" if hard_ok else "FAILED",
                    "run_name": "CP-SAT",
                    "solver_type": "CP_SAT_ONLY",
                    "_solver_result": {
                        "run_name": "CP-SAT",
                        "solver_type": "CP_SAT_ONLY",
                        "run_status": "COMPLETED" if hard_ok else "FAILED",
                        "objective_score": getattr(result, "objective_score", None),
                        "solver_stats": getattr(result, "solver_stats", {}) or {},
                        "best_bound": getattr(result, "best_objective_bound", None),
                        "optimality_gap": getattr(result, "optimality_gap", None),
                        "solve_time_seconds": getattr(result, "solve_time_seconds", None),
                        "entries_written": result.entries_written,
                        "hard_constraints_satisfied": hard_ok,
                        "cp_sat_repair_applied": False,
                        "reason_summary": getattr(result, "reason_summary", None),
                        "warnings": getattr(result, "warnings", []) or [],
                        "diagnostics": (capacity_diagnostics + (getattr(result, "diagnostics", []) or [])),
                        "message": getattr(result, "message", None),
                    },
                }
                db.commit()
            except Exception:
                pass

    except Exception as exc:
        logger.exception("_global_solve_body crashed for run %s", run_id)
        try:
            db.rollback()
            fresh = db.get(TimetableRun, run_id)
            if fresh is not None and str(fresh.status) in {"CREATED", ""}:
                fresh.status = "ERROR"
                fresh.parameters = {**(fresh.parameters or {}), "run_status": "FAILED"}
                fresh.notes = f"{type(exc).__name__}: {str(exc)}"[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/solve-global", response_model=SolveTimetableResponse)
def solve_timetable_global(
    payload: SolveGlobalTimetableRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    """Program-wide solve: returns RUNNING immediately, solves in background thread.

    Poll GET /api/solver/runs/{run_id} to track completion.
    """
    try:
        max_time_seconds = float(payload.max_time_seconds)
        if settings.environment.lower() == "production":
            max_time_seconds = min(max_time_seconds, 300.0)

        validate_db_connection(db)

        selected_ay = None
        if payload.academic_year_number is not None:
            selected_ay = _get_academic_year(db, int(payload.academic_year_number), tenant_id=tenant_id)

        run = TimetableRun(
            tenant_id=tenant_id,
            academic_year_id=(selected_ay.id if selected_ay is not None else None),
            seed=payload.seed,
            status="CREATED",
            solver_version=(
                "GA"
                if str(payload.solver_type).upper() == "GA_ONLY"
                else ("GA+CP-SAT" if str(payload.solver_type).upper() == "HYBRID" else "CP-SAT")
            ),
            parameters={
                "program_code": payload.program_code,
                "max_time_seconds": max_time_seconds,
                "relax_teacher_load_limits": payload.relax_teacher_load_limits,
                "require_optimal": payload.require_optimal,
                "solver_type": payload.solver_type,
                "run_name": (
                    "GA"
                    if str(payload.solver_type).upper() == "GA_ONLY"
                    else ("GA+CP-SAT" if str(payload.solver_type).upper() == "HYBRID" else "CP-SAT")
                ),
                "run_status": "RUNNING",
                "scope": ("YEAR_ONLY" if payload.academic_year_number is not None else "PROGRAM_GLOBAL"),
                **({"academic_year_number": int(payload.academic_year_number)} if payload.academic_year_number is not None else {}),
                **({"tenant_id": str(tenant_id)} if tenant_id is not None else {}),
            },
        )
        db.add(run)
        db.flush()
        db.commit()
        run_id = run.id

        threading.Thread(
            target=_global_solve_body,
            args=(run_id, payload.model_copy(), tenant_id, max_time_seconds),
            daemon=True,
        ).start()

        return SolveTimetableResponse(
            run_id=run_id,
            status="RUNNING",
            entries_written=0,
            run_name=(
                "GA"
                if str(payload.solver_type).upper() == "GA_ONLY"
                else ("GA+CP-SAT" if str(payload.solver_type).upper() == "HYBRID" else "CP-SAT")
            ),
            solver_type=payload.solver_type,
        )

    except DatabaseUnavailableError:
        db.rollback()
        raise
    except SAOperationalError as exc:
        db.rollback()
        if is_transient_db_connectivity_error(exc):
            raise DatabaseUnavailableError("Database temporarily unavailable") from exc
        raise
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("/api/solver/solve-global startup failed")
        raise HTTPException(
            status_code=500,
            detail={"error": "SOLVER_STARTUP_ERROR", "message": str(exc)},
        )

