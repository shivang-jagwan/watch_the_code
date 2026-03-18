from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.tenant import where_tenant
from core.db import table_exists
from models.room import Room
from models.section import Section
from models.section_subject import SectionSubject
from models.section_time_window import SectionTimeWindow
from models.subject import Subject
from models.subject_allowed_room import SubjectAllowedRoom
from models.teacher import Teacher
from models.teacher_subject_section import TeacherSubjectSection
from models.teacher_time_window import TeacherTimeWindow
from models.time_slot import TimeSlot
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun
from solver.hybrid import EvolutionConfig, run_hybrid_chronogen


def _room_type_to_hybrid(room_type: str) -> str:
    rt = str(room_type).upper()
    if rt == "LAB":
        return "LAB"
    return "THEORY"


def _subject_room_type(subject_type: str) -> str:
    st = str(subject_type).upper()
    return "LAB" if st == "LAB" else "THEORY"


def _slot_to_obj(day: int, period: int) -> dict[str, int]:
    return {"day": int(day), "period": int(period)}


def build_problem_data_from_db(
    db: Session,
    *,
    program_id,
    tenant_id,
) -> dict[str, Any]:
    q_sections = (
        select(Section)
        .where(Section.program_id == program_id)
        .where(Section.is_active.is_(True))
    )
    q_sections = where_tenant(q_sections, Section, tenant_id).order_by(Section.code)
    sections = db.execute(q_sections).scalars().all()

    q_slots = where_tenant(select(TimeSlot), TimeSlot, tenant_id)
    slots = db.execute(q_slots).scalars().all()
    if not slots:
        raise ValueError("No time slots configured")

    max_day = max(int(s.day_of_week) for s in slots)
    max_period = max(int(s.slot_index) for s in slots)
    lunch_break = sorted({int(s.slot_index) for s in slots if bool(getattr(s, "is_lunch_break", False))})

    all_non_lunch_slots = {
        (int(s.day_of_week), int(s.slot_index))
        for s in slots
        if not bool(getattr(s, "is_lunch_break", False))
    }

    q_rooms = where_tenant(select(Room).where(Room.is_active.is_(True)), Room, tenant_id)
    rooms_db = db.execute(q_rooms).scalars().all()
    rooms = [
        {
            "id": str(r.id),
            "capacity": int(getattr(r, "capacity", 0) or 0),
            "type": _room_type_to_hybrid(str(r.room_type)),
            "available_periods": [_slot_to_obj(d, p) for d, p in sorted(all_non_lunch_slots)],
        }
        for r in rooms_db
        if not bool(getattr(r, "is_special", False))
    ]

    q_subjects = (
        select(Subject)
        .where(Subject.program_id == program_id)
        .where(Subject.is_active.is_(True))
    )
    q_subjects = where_tenant(q_subjects, Subject, tenant_id)
    subjects_db = db.execute(q_subjects).scalars().all()
    subject_ids = {s.id for s in subjects_db}
    subjects = [
        {
            "id": str(s.id),
            "requires_room_type": _subject_room_type(str(s.subject_type)),
            "min_lectures_per_week": int(getattr(s, "sessions_per_week", 0) or 0),
            "split_allowed": bool(str(s.subject_type).upper() != "LAB"),
        }
        for s in subjects_db
    ]

    q_tss = (
        select(TeacherSubjectSection)
        .where(TeacherSubjectSection.is_active.is_(True))
        .where(TeacherSubjectSection.subject_id.in_(list(subject_ids)) if subject_ids else False)
    )
    q_tss = where_tenant(q_tss, TeacherSubjectSection, tenant_id)
    tss_rows = db.execute(q_tss).scalars().all()

    teacher_subjects: dict[Any, set[Any]] = defaultdict(set)
    teacher_by_section_subject: dict[tuple[Any, Any], Any] = {}
    for row in tss_rows:
        teacher_subjects[row.teacher_id].add(row.subject_id)
        teacher_by_section_subject[(row.section_id, row.subject_id)] = row.teacher_id

    q_teachers = where_tenant(select(Teacher).where(Teacher.is_active.is_(True)), Teacher, tenant_id)
    teachers_db = db.execute(q_teachers).scalars().all()

    q_teacher_windows = where_tenant(select(TeacherTimeWindow), TeacherTimeWindow, tenant_id)
    tw_rows = db.execute(q_teacher_windows).scalars().all()
    tw_by_teacher: dict[Any, list[TeacherTimeWindow]] = defaultdict(list)
    for tw in tw_rows:
        tw_by_teacher[tw.teacher_id].append(tw)

    teacher_objs = []
    for t in teachers_db:
        windows = tw_by_teacher.get(t.id, [])
        if windows:
            availability = set()
            for win in windows:
                days = range(max_day + 1) if win.day_of_week is None else [int(win.day_of_week)]
                for d in days:
                    for p in range(int(win.start_slot_index), int(win.end_slot_index) + 1):
                        if (d, p) in all_non_lunch_slots:
                            availability.add((d, p))
        else:
            availability = set(all_non_lunch_slots)

        if getattr(t, "weekly_off_day", None) is not None:
            off = int(t.weekly_off_day)
            availability = {(d, p) for (d, p) in availability if d != off}

        teacher_objs.append(
            {
                "id": str(t.id),
                "subjects": [str(sid) for sid in sorted(teacher_subjects.get(t.id, set()), key=lambda x: str(x))],
                "max_lectures_per_week": int(getattr(t, "max_per_week", 0) or 0),
                "availability": [_slot_to_obj(d, p) for d, p in sorted(availability)],
                "preferences": [],
            }
        )

    q_section_subject = where_tenant(select(SectionSubject), SectionSubject, tenant_id)
    sec_sub_rows = db.execute(q_section_subject).scalars().all()
    by_section: dict[Any, list[Any]] = defaultdict(list)
    for ss in sec_sub_rows:
        if ss.subject_id in subject_ids:
            by_section[ss.section_id].append(ss.subject_id)

    q_section_windows = where_tenant(select(SectionTimeWindow), SectionTimeWindow, tenant_id)
    sw_rows = db.execute(q_section_windows).scalars().all()
    windows_by_section: dict[Any, list[SectionTimeWindow]] = defaultdict(list)
    for sw in sw_rows:
        windows_by_section[sw.section_id].append(sw)

    classes = []
    for sec in sections:
        curriculum = []
        allowed_subjects = by_section.get(sec.id, [])
        for subj_id in allowed_subjects:
            tid = teacher_by_section_subject.get((sec.id, subj_id))
            if tid is None:
                continue
            subj = next((s for s in subjects_db if s.id == subj_id), None)
            if subj is None:
                continue
            curriculum.append(
                {
                    "subject_id": str(subj_id),
                    "teacher_id": str(tid),
                    "min_per_week": int(getattr(subj, "sessions_per_week", 0) or 0),
                }
            )

        if not curriculum:
            continue

        classes.append(
            {
                "id": str(sec.id),
                "curriculum": curriculum,
            }
        )

    exclusive = []
    if table_exists(db, "subject_allowed_rooms"):
        q_sar = where_tenant(select(SubjectAllowedRoom), SubjectAllowedRoom, tenant_id)
        sar_rows = db.execute(q_sar).scalars().all()
        for row in sar_rows:
            if bool(getattr(row, "is_exclusive", False)):
                exclusive.append({"room_id": str(row.room_id), "subject_id": str(row.subject_id)})

    return {
        "institution": {
            "days_per_week": int(max_day + 1),
            "periods_per_day": int(max_period + 1),
            "lunch_break": lunch_break,
        },
        "rooms": rooms,
        "subjects": subjects,
        "teachers": teacher_objs,
        "classes": classes,
        "exclusive_room_ownership": exclusive,
    }


def run_and_persist_dual_solver(
    db: Session,
    *,
    run: TimetableRun,
    program_id,
    tenant_id,
    solver_type: Literal["GA_ONLY", "HYBRID"],
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if solver_type not in {"GA_ONLY", "HYBRID"}:
        raise ValueError(f"Unsupported solver_type: {solver_type}")

    data = build_problem_data_from_db(db, program_id=program_id, tenant_id=tenant_id)
    cfg_data = dict(config_overrides or {})
    cfg_data["solver_type"] = solver_type
    cfg = EvolutionConfig(**cfg_data)

    result = run_hybrid_chronogen(data=data, config=cfg, output_dir=None)

    # Replace entries for this run (idempotent reruns).
    db.query(TimetableEntry).filter(TimetableEntry.run_id == run.id).delete(synchronize_session=False)

    q_sections = where_tenant(select(Section), Section, tenant_id)
    sections = db.execute(q_sections).scalars().all()
    year_by_section = {str(s.id): s.academic_year_id for s in sections}

    q_slots = where_tenant(select(TimeSlot), TimeSlot, tenant_id)
    slots = db.execute(q_slots).scalars().all()
    slot_by_pair = {(int(s.day_of_week), int(s.slot_index)): s.id for s in slots}

    for g in result.best_chromosome.genes:
        slot_id = slot_by_pair.get((int(g.day), int(g.period)))
        sec_year = year_by_section.get(str(g.class_id))
        if slot_id is None or sec_year is None:
            continue

        entry = TimetableEntry(
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=sec_year,
            section_id=uuid.UUID(str(g.class_id)),
            subject_id=uuid.UUID(str(g.subject_id)),
            teacher_id=uuid.UUID(str(g.teacher_id)),
            room_id=uuid.UUID(str(g.room_id)),
            slot_id=slot_id,
        )
        db.add(entry)

    b = result.best_breakdown
    hard_conflicts = int(b.teacher_conflicts + b.room_conflicts + b.class_conflicts + b.exclusive_room_violations)

    if solver_type == "GA_ONLY" and hard_conflicts > 0:
        db.add(
            TimetableConflict(
                tenant_id=tenant_id,
                run_id=run.id,
                severity="WARN",
                conflict_type="GA_ONLY_CONFLICTS",
                message=f"GA Only run completed with {hard_conflicts} hard-constraint conflict penalty count.",
                metadata_json={
                    "teacher_conflicts": b.teacher_conflicts,
                    "room_conflicts": b.room_conflicts,
                    "class_conflicts": b.class_conflicts,
                    "exclusive_room_violations": b.exclusive_room_violations,
                },
            )
        )

    run_name = "GA" if solver_type == "GA_ONLY" else "GA+CP-SAT"
    run.status = "OPTIMAL" if hard_conflicts == 0 else "FEASIBLE"
    run.solver_version = run_name
    run.parameters = {
        **(run.parameters or {}),
        "run_status": "COMPLETED",
        "run_name": run_name,
        "solver_type": solver_type,
        "_solver_result": {
            "run_name": run_name,
            "solver_type": solver_type,
            "run_status": "COMPLETED",
            "best_fitness": result.best_fitness,
            "generation_count": result.generations_ran,
            "entries_written": len(result.best_chromosome.genes),
            "breakdown": b.__dict__,
            "history_best": list(result.history_best),
            "history_mean": list(result.history_mean),
            "hard_constraints_satisfied": hard_conflicts == 0,
            # Full timetable payload retained for run-level retrieval and comparison.
            "timetable_data": [
                {
                    "event_id": g.event_id,
                    "class_id": str(g.class_id),
                    "subject_id": str(g.subject_id),
                    "teacher_id": str(g.teacher_id),
                    "room_id": str(g.room_id),
                    "day": int(g.day),
                    "period": int(g.period),
                }
                for g in result.best_chromosome.genes
            ],
        },
    }

    db.commit()

    return {
        "run_name": run_name,
        "solver_type": solver_type,
        "best_fitness": result.best_fitness,
        "generation_count": result.generations_ran,
        "entries_written": len(result.best_chromosome.genes),
        "hard_constraints_satisfied": hard_conflicts == 0,
    }
