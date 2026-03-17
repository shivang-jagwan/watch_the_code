from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import func, select, literal
from sqlalchemy.orm import Session

from api.tenant import where_tenant
from core.db import table_exists
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.combined_subject_group import CombinedSubjectGroup
from models.combined_subject_section import CombinedSubjectSection
from models.elective_block import ElectiveBlock
from models.elective_block_subject import ElectiveBlockSubject
from models.room import Room
from models.section_elective_block import SectionElectiveBlock
from models.section_subject import SectionSubject
from models.section_break import SectionBreak
from models.section_time_window import SectionTimeWindow
from models.subject import Subject
from models.subject_allowed_room import SubjectAllowedRoom
from models.teacher import Teacher
from models.teacher_subject_section import TeacherSubjectSection
from models.time_slot import TimeSlot
from models.fixed_timetable_entry import FixedTimetableEntry
from models.special_allotment import SpecialAllotment
from models.timetable_conflict import TimetableConflict
from models.timetable_run import TimetableRun
from models.curriculum_subject import CurriculumSubject
from models.track_subject import TrackSubject


@dataclass(frozen=True)
class ValidationConflict:
    conflict_type: str
    message: str
    severity: str = "ERROR"
    section_id: Any | None = None
    teacher_id: Any | None = None
    subject_id: Any | None = None
    room_id: Any | None = None
    slot_id: Any | None = None
    metadata: dict[str, Any] | None = None


def persist_conflicts(db: Session, *, run: TimetableRun, conflicts: Iterable[ValidationConflict]) -> None:
    tenant_id = getattr(run, "tenant_id", None)
    for c in conflicts:
        payload = c.metadata or {}
        db.add(
            TimetableConflict(
                tenant_id=tenant_id,
                run_id=run.id,
                severity=c.severity,
                conflict_type=c.conflict_type,
                message=c.message,
                section_id=c.section_id,
                teacher_id=c.teacher_id,
                subject_id=c.subject_id,
                room_id=c.room_id,
                slot_id=c.slot_id,
                details_json=payload,
                metadata_json=payload,
            )
        )


def validate_prereqs(
    db: Session,
    *,
    run: TimetableRun,
    program_id,
    academic_year_id,
    sections: list,
) -> list[ValidationConflict]:
    conflicts: list[ValidationConflict] = []
    tenant_id = getattr(run, "tenant_id", None)

    # Global validation:
    # - If academic_year_id is provided: solve is scoped to one academic year.
    # - If academic_year_id is None: program-wide solve spans multiple academic years.
    solve_year_ids = sorted({s.academic_year_id for s in sections if getattr(s, "academic_year_id", None) is not None})
    if academic_year_id is not None:
        solve_year_ids = [academic_year_id]

    section_ids = [s.id for s in sections]

    use_elective_blocks = (
        table_exists(db, "elective_blocks")
        and table_exists(db, "elective_block_subjects")
        and table_exists(db, "section_elective_blocks")
    )

    # Assignment lookup used by multiple validations (including combined-group teacher inference).
    # Must be initialized even when no fixed entries / special allotments exist.
    assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = {}
    if section_ids:
        assign_rows = (
            db.execute(
                where_tenant(
                    select(
                        TeacherSubjectSection.section_id,
                        TeacherSubjectSection.subject_id,
                        TeacherSubjectSection.teacher_id,
                    )
                    .where(TeacherSubjectSection.section_id.in_(section_ids))
                    .where(TeacherSubjectSection.is_active.is_(True)),
                    TeacherSubjectSection,
                    tenant_id,
                )
            )
            .all()
        )
        for sec_id, subj_id, teacher_id in assign_rows:
            # If duplicates exist, the strict assignment validation should report it.
            assigned_teacher_by_section_subject.setdefault((sec_id, subj_id), teacher_id)

    mapped_subject_ids_by_section = defaultdict(list)
    if section_ids:
        q_sec_subj = select(SectionSubject.section_id, SectionSubject.subject_id).where(
            SectionSubject.section_id.in_(section_ids)
        )
        q_sec_subj = where_tenant(q_sec_subj, SectionSubject, tenant_id)
        for sec_id, subj_id in db.execute(q_sec_subj).all():
            mapped_subject_ids_by_section[sec_id].append(subj_id)

    use_curriculum_subjects = table_exists(db, "curriculum_subjects")
    curriculum_by_year_track: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    if use_curriculum_subjects:
        q_curr = select(CurriculumSubject).where(CurriculumSubject.program_id == program_id)
        if solve_year_ids:
            q_curr = q_curr.where(CurriculumSubject.academic_year_id.in_(solve_year_ids))
        q_curr = where_tenant(q_curr, CurriculumSubject, tenant_id)
        for row in db.execute(q_curr).scalars().all():
            curriculum_by_year_track[(row.academic_year_id, str(row.track))].append(row)
    else:
        q_track = select(TrackSubject).where(TrackSubject.program_id == program_id)
        if solve_year_ids:
            q_track = q_track.where(TrackSubject.academic_year_id.in_(solve_year_ids))
        q_track = where_tenant(q_track, TrackSubject, tenant_id)
        for row in db.execute(q_track).scalars().all():
            curriculum_by_year_track[(row.academic_year_id, str(row.track))].append(row)

    # Time slots are required.
    if db.execute(where_tenant(select(TimeSlot.id).limit(1), TimeSlot, tenant_id)).first() is None:
        conflicts.append(
            ValidationConflict(
                conflict_type="MISSING_TIME_SLOTS",
                message="No time slots configured. Populate time_slots before generating timetables.",
            )
        )

    # Exclusive subject-room validation.
    if table_exists(db, "subject_allowed_rooms"):
        q_active_subjects = where_tenant(
            select(Subject.id)
            .where(Subject.is_active.is_(True))
            .where(Subject.program_id == program_id),
            Subject,
            tenant_id,
        )
        if solve_year_ids:
            q_active_subjects = q_active_subjects.where(Subject.academic_year_id.in_(solve_year_ids))
        active_subject_ids = set(db.execute(q_active_subjects).scalars().all())

        q_exclusive = where_tenant(
            select(SubjectAllowedRoom.room_id, SubjectAllowedRoom.subject_id)
            .where(SubjectAllowedRoom.is_exclusive.is_(True)),
            SubjectAllowedRoom,
            tenant_id,
        )
        subjects_by_room: dict[Any, set[Any]] = defaultdict(set)
        for room_id, subject_id in db.execute(q_exclusive).all():
            if subject_id not in active_subject_ids:
                continue
            subjects_by_room[room_id].add(subject_id)

        if subjects_by_room:
            room_ids = list(subjects_by_room.keys())
            room_rows = db.execute(
                where_tenant(
                    select(Room.id, Room.code, Room.is_active).where(Room.id.in_(room_ids)),
                    Room,
                    tenant_id,
                )
            ).all()
            room_code_by_id = {rid: code for rid, code, _active in room_rows}
            room_active_by_id = {rid: bool(active) for rid, _code, active in room_rows}
            for room_id, subject_ids in subjects_by_room.items():
                if not room_active_by_id.get(room_id, False):
                    continue
                if len(subject_ids) <= 1:
                    continue
                room_code = str(room_code_by_id.get(room_id) or room_id)
                conflicts.append(
                    ValidationConflict(
                        conflict_type="ROOM_CONFLICT",
                        message=f"Room {room_code} assigned exclusively to multiple subjects",
                        room_id=room_id,
                        metadata={
                            "error": "ROOM_CONFLICT",
                            "room_code": room_code,
                            "subject_count": len(subject_ids),
                        },
                    )
                )

    # Section time windows must exist for all active days and use valid slot indices.
    # Active days = days that have at least one time slot.
    slot_rows = db.execute(
        where_tenant(select(TimeSlot.day_of_week, TimeSlot.slot_index, TimeSlot.id), TimeSlot, tenant_id)
    ).all()
    active_days: list[int] = sorted({int(d) for d, _i, _id in slot_rows})
    slot_indices_by_day: dict[int, set[int]] = defaultdict(set)
    slot_id_to_day_index: dict[Any, tuple[int, int]] = {}
    slot_id_by_day_index: dict[tuple[int, int], Any] = {}
    for d, i, sid in slot_rows:
        slot_indices_by_day[int(d)].add(int(i))
        slot_id_to_day_index[sid] = (int(d), int(i))
        slot_id_by_day_index[(int(d), int(i))] = sid

    windows = []
    if section_ids:
        q_windows = select(SectionTimeWindow).where(SectionTimeWindow.section_id.in_(section_ids))
        q_windows = where_tenant(q_windows, SectionTimeWindow, tenant_id)
        windows = db.execute(q_windows).scalars().all()
    windows_by_section_day: dict[tuple[Any, int], SectionTimeWindow] = {}
    duplicate_window_days: set[tuple[Any, int]] = set()
    for w in windows:
        key = (w.section_id, int(w.day_of_week))
        if key in windows_by_section_day:
            duplicate_window_days.add(key)
        else:
            windows_by_section_day[key] = w

    for section in sections:
        if not active_days:
            # Missing time slots is already reported above.
            continue

        for d in active_days:
            key = (section.id, d)
            if key in duplicate_window_days:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="DUPLICATE_SECTION_TIME_WINDOW",
                        message="Section has multiple time windows for the same day; expected exactly one.",
                        section_id=section.id,
                        metadata={"day_of_week": d},
                    )
                )
                continue

            w = windows_by_section_day.get(key)
            if w is None:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="MISSING_SECTION_TIME_WINDOW",
                        message="Section is missing a time window for an active day.",
                        section_id=section.id,
                        metadata={"day_of_week": d},
                    )
                )
                continue

            valid_indices = slot_indices_by_day.get(d, set())
            if int(w.start_slot_index) not in valid_indices:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="INVALID_SECTION_TIME_WINDOW",
                        message="Section time window start_slot_index does not exist in time_slots for this day.",
                        section_id=section.id,
                        metadata={"day_of_week": d, "start_slot_index": int(w.start_slot_index)},
                    )
                )
            if int(w.end_slot_index) not in valid_indices:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="INVALID_SECTION_TIME_WINDOW",
                        message="Section time window end_slot_index does not exist in time_slots for this day.",
                        section_id=section.id,
                        metadata={"day_of_week": d, "end_slot_index": int(w.end_slot_index)},
                    )
                )
            if int(w.end_slot_index) < int(w.start_slot_index):
                conflicts.append(
                    ValidationConflict(
                        conflict_type="INVALID_SECTION_TIME_WINDOW",
                        message="Section time window end_slot_index must be >= start_slot_index.",
                        section_id=section.id,
                        metadata={
                            "day_of_week": d,
                            "start_slot_index": int(w.start_slot_index),
                            "end_slot_index": int(w.end_slot_index),
                        },
                    )
                )

    # Break compatibility: any breaks defined for this run must fall inside the section window.
    # Also used later to ensure fixed/special locks do not overlap breaks.
    break_slot_ids_by_section: dict[Any, set[Any]] = defaultdict(set)
    if section_ids:
        q_breaks = select(SectionBreak).where(SectionBreak.run_id == run.id).where(SectionBreak.section_id.in_(section_ids))
        q_breaks = where_tenant(q_breaks, SectionBreak, tenant_id)
        breaks = db.execute(q_breaks).scalars().all()
        for b in breaks:
            break_slot_ids_by_section[b.section_id].add(b.slot_id)

            day_idx = slot_id_to_day_index.get(b.slot_id)
            if day_idx is None:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="INVALID_SECTION_BREAK",
                        message="Break references a time slot that does not exist.",
                        section_id=b.section_id,
                        slot_id=b.slot_id,
                    )
                )
                continue
            d, si = day_idx
            w = windows_by_section_day.get((b.section_id, d))
            if w is None:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="BREAK_OUTSIDE_SECTION_WINDOW",
                        message="Break is set on a day where the section has no working window.",
                        section_id=b.section_id,
                        slot_id=b.slot_id,
                        metadata={"day_of_week": d, "slot_index": si},
                    )
                )
                continue
            if si < int(w.start_slot_index) or si > int(w.end_slot_index):
                conflicts.append(
                    ValidationConflict(
                        conflict_type="BREAK_OUTSIDE_SECTION_WINDOW",
                        message="Break slot is outside the section's working window.",
                        section_id=b.section_id,
                        slot_id=b.slot_id,
                        metadata={
                            "day_of_week": d,
                            "slot_index": si,
                            "window_start": int(w.start_slot_index),
                            "window_end": int(w.end_slot_index),
                        },
                    )
                )

    # Rooms are required.
    q_room_any = where_tenant(select(Room.id).limit(1), Room, tenant_id)
    if db.execute(q_room_any).first() is None:
        conflicts.append(
            ValidationConflict(
                conflict_type="MISSING_ROOMS",
                message="No rooms configured. Populate rooms before generating timetables.",
            )
        )

    # Non-special rooms are required for auto-assigned room solving.
    if (
        db.execute(
            where_tenant(
                select(Room.id).where(Room.is_active.is_(True)).where(Room.is_special.is_(False)).limit(1),
                Room,
                tenant_id,
            )
        ).first()
        is None
    ):
        conflicts.append(
            ValidationConflict(
                conflict_type="MISSING_NON_SPECIAL_ROOMS",
                message="No non-special active rooms configured. Normal timetable entries cannot be assigned rooms.",
            )
        )

    # (Legacy) minimum window check is now covered by the per-day validation above.

    # Curriculum must exist per section unless explicit mapping is present.
    for section in sections:
        if mapped_subject_ids_by_section.get(section.id):
            continue

        effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
        rows = curriculum_by_year_track.get((effective_year_id, str(section.track)), [])
        has_any_track = bool(rows)
        if not has_any_track:
            conflicts.append(
                ValidationConflict(
                    conflict_type="MISSING_TRACK_CURRICULUM",
                    message=(
                        f"No curriculum_subjects configured for track '{section.track}'."
                        if use_curriculum_subjects
                        else f"No track_subjects configured for track '{section.track}'."
                    ),
                    section_id=section.id,
                    metadata={"track": section.track, "academic_year_id": str(effective_year_id)},
                )
            )

    # Elective rules (new): electives are modeled via elective blocks.
    # If a section has electives configured in curriculum, it must either:
    # - use explicit section_subjects mapping (override), OR
    # - be mapped to at least one elective block.
    #
    # Note: blocks can also be used for a single elective (legacy conversion); we warn
    # when a block has fewer than 2 assignments.
    blocks_by_section: dict[Any, list[Any]] = defaultdict(list)
    if use_elective_blocks and section_ids:
        sec_block_rows = (
            db.execute(
                where_tenant(
                    select(SectionElectiveBlock.section_id, SectionElectiveBlock.block_id)
                    .where(SectionElectiveBlock.section_id.in_(section_ids)),
                    SectionElectiveBlock,
                    tenant_id,
                )
            )
            .all()
        )
        for sid, bid in sec_block_rows:
            blocks_by_section[sid].append(bid)

    for section in sections:
        if mapped_subject_ids_by_section.get(section.id):
            continue

        effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
        rows = curriculum_by_year_track.get((effective_year_id, str(section.track)), [])
        elective_subject_ids = [r.subject_id for r in rows if bool(getattr(r, "is_elective", False))]

        if elective_subject_ids and not blocks_by_section.get(section.id):
            conflicts.append(
                ValidationConflict(
                    conflict_type=("MISSING_ELECTIVE_BLOCKS" if use_elective_blocks else "ELECTIVE_BLOCK_TABLES_MISSING"),
                    message=(
                        "Section has elective options configured but is not mapped to any elective blocks."
                        if use_elective_blocks
                        else "Electives are configured but elective-block tables are missing. Apply DB migrations to enable elective blocks."
                    ),
                    section_id=section.id,
                    metadata={"track": str(section.track), "academic_year_id": str(effective_year_id)},
                )
            )

    # Teacher assignment validation (strict): each (section, required subject) must have exactly one active teacher.
    # We validate against the curriculum per section (mapping override else track + electives).
    if section_ids:
        # Elective blocks mapped to sections in this solve.
        block_ids: list[Any] = []
        blocks_by_section = defaultdict(list)
        blocks_by_id: dict[Any, ElectiveBlock] = {}
        block_subjects_by_block: dict[Any, list[tuple[Any, Any]]] = defaultdict(list)  # block_id -> [(subject_id, teacher_id)]

        if use_elective_blocks:
            section_blocks_rows = (
                db.execute(
                    where_tenant(
                        select(SectionElectiveBlock.section_id, SectionElectiveBlock.block_id)
                        .where(SectionElectiveBlock.section_id.in_(section_ids)),
                        SectionElectiveBlock,
                        tenant_id,
                    )
                )
                .all()
            )
            block_ids = sorted({bid for _sid, bid in section_blocks_rows})
            for sid, bid in section_blocks_rows:
                blocks_by_section[sid].append(bid)

            if block_ids:
                block_rows = (
                    db.execute(
                        where_tenant(select(ElectiveBlock).where(ElectiveBlock.id.in_(block_ids)), ElectiveBlock, tenant_id)
                    )
                    .scalars()
                    .all()
                )
                blocks_by_id = {b.id: b for b in block_rows}

                block_subject_rows = db.execute(
                    where_tenant(
                        select(
                            ElectiveBlockSubject.block_id,
                            ElectiveBlockSubject.subject_id,
                            ElectiveBlockSubject.teacher_id,
                        ).where(ElectiveBlockSubject.block_id.in_(block_ids)),
                        ElectiveBlockSubject,
                        tenant_id,
                    )
                ).all()
                for bid, subj_id, teacher_id in block_subject_rows:
                    block_subjects_by_block[bid].append((subj_id, teacher_id))

        # Allowed subject ids per section (mapping override else track curriculum).
        allowed_subject_ids_by_section: dict[Any, set[Any]] = {}

        for section in sections:
            mapped = mapped_subject_ids_by_section.get(section.id, [])
            if mapped:
                allowed_subject_ids_by_section[section.id] = set(mapped)
                continue

            effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
            rows = curriculum_by_year_track.get((effective_year_id, str(section.track)), [])
            mandatory = [r for r in rows if not bool(getattr(r, "is_elective", False))]
            allowed = {r.subject_id for r in mandatory}
            allowed_subject_ids_by_section[section.id] = allowed

        required_pairs: list[tuple[Any, Any]] = []
        for sec_id, subj_ids in allowed_subject_ids_by_section.items():
            for sid in subj_ids:
                required_pairs.append((sec_id, sid))

        # Load active assignments for the sections in this solve.
        assignment_rows = (
            db.execute(
                where_tenant(
                    select(TeacherSubjectSection.section_id, TeacherSubjectSection.subject_id, TeacherSubjectSection.teacher_id)
                    .where(TeacherSubjectSection.section_id.in_(section_ids))
                    .where(TeacherSubjectSection.is_active.is_(True)),
                    TeacherSubjectSection,
                    tenant_id,
                )
            )
            .all()
        )
        teachers_by_section_subject: dict[tuple[Any, Any], set[Any]] = defaultdict(set)
        for sec_id, subj_id, teacher_id in assignment_rows:
            teachers_by_section_subject[(sec_id, subj_id)].add(teacher_id)

        # Elective block validation:
        # - Each section's mapped blocks must be in-scope and active
        # - Each block must have >= 1 subject assignment
        # - No duplicate teacher within a block
        # - Each (subject, teacher) must be eligible for that section (teacher_subject_sections)
        # - All subjects in the block must have equal sessions_per_week (>0)
        if block_ids:
            all_block_subject_ids = sorted({sid for pairs in block_subjects_by_block.values() for sid, _tid in pairs})
            subj_rows = []
            if all_block_subject_ids:
                subj_rows = (
                    db.execute(where_tenant(select(Subject).where(Subject.id.in_(all_block_subject_ids)), Subject, tenant_id))
                    .scalars()
                    .all()
                )
            subj_by_id = {s.id: s for s in subj_rows}

            for section in sections:
                sec_block_ids = blocks_by_section.get(section.id, [])
                if not sec_block_ids:
                    continue

                # If explicit section-subject override is present, reject mixing with blocks.
                if mapped_subject_ids_by_section.get(section.id):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SECTION_MAPPING_CONFLICT",
                            message="Section has explicit subject mapping (section_subjects) and elective blocks. Use only one approach.",
                            severity="WARN",
                            section_id=section.id,
                            metadata={"block_ids": [str(bid) for bid in sec_block_ids]},
                        )
                    )
                    continue

                for bid in sec_block_ids:
                    block = blocks_by_id.get(bid)
                    if block is None:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="ELECTIVE_BLOCK_NOT_FOUND",
                                message="Elective block mapping references a block that does not exist.",
                                section_id=section.id,
                                metadata={"block_id": str(bid)},
                            )
                        )
                        continue
                    if not bool(getattr(block, "is_active", True)):
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="ELECTIVE_BLOCK_INACTIVE",
                                message="Elective block is inactive.",
                                section_id=section.id,
                                metadata={"block_id": str(bid)},
                            )
                        )
                        continue
                    if block.academic_year_id != section.academic_year_id:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="ELECTIVE_BLOCK_OUT_OF_SCOPE",
                                message="Elective block scope does not match the section (academic year).",
                                section_id=section.id,
                                metadata={"block_id": str(bid)},
                            )
                        )
                        continue

                    pairs = block_subjects_by_block.get(bid, [])
                    if not pairs:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="ELECTIVE_BLOCK_EMPTY",
                                message="Elective block has no subject-teacher assignments.",
                                section_id=section.id,
                                metadata={"block_id": str(bid)},
                            )
                        )
                        continue

                    if len(pairs) < 2:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="ELECTIVE_BLOCK_TOO_SMALL",
                                message="Elective block has fewer than 2 subject-teacher assignments (parallel electives).",
                                severity="WARN",
                                section_id=section.id,
                                metadata={"block_id": str(bid), "assignments": int(len(pairs))},
                            )
                        )

                    # Duplicate teacher within the same block.
                    teacher_ids = [tid for _sid, tid in pairs]
                    if len(set(teacher_ids)) != len(teacher_ids):
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="DUPLICATE_TEACHER_IN_BLOCK",
                                message="A teacher is assigned multiple times within the same elective block.",
                                section_id=section.id,
                                metadata={"block_id": str(bid)},
                            )
                        )

                    sessions_vals: list[int] = []
                    for subj_id, teacher_id in pairs:
                        subj = subj_by_id.get(subj_id)
                        if subj is None:
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="SUBJECT_NOT_FOUND",
                                    message="Elective block references a subject that does not exist.",
                                    section_id=section.id,
                                    subject_id=subj_id,
                                    metadata={"block_id": str(bid)},
                                )
                            )
                            continue
                        if str(subj.subject_type) != "THEORY":
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="ELECTIVE_BLOCK_SUBJECT_MUST_BE_THEORY",
                                    message="Elective blocks currently support THEORY subjects only.",
                                    section_id=section.id,
                                    subject_id=subj_id,
                                    metadata={"block_id": str(bid)},
                                )
                            )

                        # Subject must be marked as elective in curriculum for this section's track.
                        effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
                        track_rows = curriculum_by_year_track.get((effective_year_id, str(section.track)), [])
                        elective_ids_for_track = {r.subject_id for r in track_rows if bool(getattr(r, "is_elective", False))}
                        if elective_ids_for_track and subj_id not in elective_ids_for_track:
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="ELECTIVE_BLOCK_SUBJECT_NOT_ELECTIVE",
                                    message=(
                                        "Elective block contains a subject that is not marked as elective in curriculum_subjects for this section's track."
                                        if use_curriculum_subjects
                                        else "Elective block contains a subject that is not marked as elective in track_subjects for this section's track."
                                    ),
                                    section_id=section.id,
                                    subject_id=subj_id,
                                    metadata={"block_id": str(bid), "track": str(section.track)},
                                )
                            )
                        sessions_vals.append(int(getattr(subj, "sessions_per_week", 0) or 0))

                        eligible = teachers_by_section_subject.get((section.id, subj_id), set())
                        if teacher_id not in eligible:
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="ELECTIVE_BLOCK_TEACHER_NOT_ELIGIBLE",
                                    message="Elective block teacher is not assigned to teach this subject in this section (teacher_subject_sections).",
                                    section_id=section.id,
                                    subject_id=subj_id,
                                    teacher_id=teacher_id,
                                    metadata={"block_id": str(bid)},
                                )
                            )

                    sessions_vals = [v for v in sessions_vals if v is not None]
                    if sessions_vals:
                        if any(v <= 0 for v in sessions_vals):
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="ELECTIVE_BLOCK_INVALID_SESSIONS",
                                    message="Elective block subjects must have sessions_per_week > 0.",
                                    section_id=section.id,
                                    metadata={"block_id": str(bid), "sessions_per_week": sessions_vals},
                                )
                            )
                        if len(set(sessions_vals)) != 1:
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="ELECTIVE_BLOCK_MISMATCHED_SESSIONS",
                                    message="All subjects in an elective block must have the same sessions_per_week.",
                                    section_id=section.id,
                                    metadata={"block_id": str(bid), "sessions_per_week": sessions_vals},
                                )
                            )

        # Exactly one teacher per section+subject.
        for sec_id, subj_id in required_pairs:
            teachers = teachers_by_section_subject.get((sec_id, subj_id), set())
            if not teachers:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="MISSING_TEACHER_ASSIGNMENT",
                        message="No teacher assigned for this section+subject (teacher_subject_sections).",
                        section_id=sec_id,
                        subject_id=subj_id,
                    )
                )
            elif len(teachers) > 1:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="DUPLICATE_TEACHER_ASSIGNMENT",
                        message="Multiple teachers assigned for the same section+subject; expected exactly one.",
                        section_id=sec_id,
                        subject_id=subj_id,
                        metadata={"teacher_ids": [str(t) for t in sorted(list(teachers), key=lambda x: str(x))]},
                    )
                )

        # Teacher weekly load sanity: total required occupied slots must not exceed max_per_week.
        subj_ids_all = sorted({sid for _sec, sid in required_pairs})
        teacher_ids_all = sorted({tid for teachers in teachers_by_section_subject.values() for tid in teachers})
        if subj_ids_all and teacher_ids_all:
            subject_rows = (
                db.execute(where_tenant(select(Subject).where(Subject.id.in_(subj_ids_all)), Subject, tenant_id)).scalars().all()
            )
            teacher_rows = (
                db.execute(where_tenant(select(Teacher).where(Teacher.id.in_(teacher_ids_all)), Teacher, tenant_id)).scalars().all()
            )
            subj_by_id = {s.id: s for s in subject_rows}
            teacher_by_id = {t.id: t for t in teacher_rows}

            # Combined THEORY groups should count once per group for teacher load.
            # Otherwise, a combined class across N sections gets incorrectly counted as N× hours.
            solve_section_ids = set(section_ids)
            combined_gid_by_sec_subj: dict[tuple[Any, Any], Any] = {}
            combined_group_sections: dict[Any, list[Any]] = defaultdict(list)  # gid -> [section_id]
            combined_group_subject: dict[Any, Any] = {}  # gid -> subject_id
            combined_group_teacher: dict[Any, Any | None] = {}  # gid -> teacher_id (optional)

            use_v2_combined = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
            if solve_section_ids:
                if use_v2_combined:
                    q_combined = (
                        select(
                            CombinedGroup.id,
                            CombinedGroup.subject_id,
                            CombinedGroup.teacher_id,
                            CombinedGroupSection.section_id,
                        )
                        .join(CombinedGroupSection, CombinedGroupSection.combined_group_id == CombinedGroup.id)
                        .join(Subject, Subject.id == CombinedGroup.subject_id)
                        .where(Subject.program_id == program_id)
                        .where(Subject.is_active.is_(True))
                    )
                    if solve_year_ids:
                        q_combined = q_combined.where(CombinedGroup.academic_year_id.in_(solve_year_ids)).where(
                            Subject.academic_year_id.in_(solve_year_ids)
                        )
                    q_combined = where_tenant(q_combined, CombinedGroup, tenant_id)
                    q_combined = where_tenant(q_combined, CombinedGroupSection, tenant_id)
                    q_combined = where_tenant(q_combined, Subject, tenant_id)
                    combined_rows = db.execute(q_combined).all()
                else:
                    q_combined = (
                        select(
                            CombinedSubjectGroup.id,
                            CombinedSubjectGroup.subject_id,
                            literal(None).label("teacher_id"),
                            CombinedSubjectSection.section_id,
                        )
                        .join(CombinedSubjectSection, CombinedSubjectSection.combined_group_id == CombinedSubjectGroup.id)
                        .join(Subject, Subject.id == CombinedSubjectGroup.subject_id)
                        .where(Subject.program_id == program_id)
                        .where(Subject.is_active.is_(True))
                    )
                    if solve_year_ids:
                        q_combined = q_combined.where(CombinedSubjectGroup.academic_year_id.in_(solve_year_ids)).where(
                            Subject.academic_year_id.in_(solve_year_ids)
                        )
                    q_combined = where_tenant(q_combined, CombinedSubjectGroup, tenant_id)
                    q_combined = where_tenant(q_combined, CombinedSubjectSection, tenant_id)
                    q_combined = where_tenant(q_combined, Subject, tenant_id)
                    combined_rows = db.execute(q_combined).all()

                for gid, subj_id, teacher_id, sec_id in combined_rows:
                    if sec_id not in solve_section_ids:
                        continue
                    subj = subj_by_id.get(subj_id)
                    if subj is None or str(getattr(subj, "subject_type", "")) != "THEORY":
                        continue
                    combined_group_sections[gid].append(sec_id)
                    combined_group_subject[gid] = subj_id
                    if gid not in combined_group_teacher:
                        combined_group_teacher[gid] = teacher_id
                    combined_gid_by_sec_subj[(sec_id, subj_id)] = gid

            teacher_required_slots = defaultdict(int)  # teacher_id -> total occupied slots/week
            teacher_affected_sections: dict[Any, set[Any]] = defaultdict(set)
            teacher_affected_subjects: dict[Any, set[Any]] = defaultdict(set)

            # 1) Count non-combined required pairs normally.
            combined_gids_seen: set[Any] = set()
            for sec_id, subj_id in required_pairs:
                gid = combined_gid_by_sec_subj.get((sec_id, subj_id))
                if gid is not None:
                    combined_gids_seen.add(gid)
                    continue

                teachers = teachers_by_section_subject.get((sec_id, subj_id), set())
                if len(teachers) != 1:
                    continue
                teacher_id = next(iter(teachers))
                subj = subj_by_id.get(subj_id)
                if subj is None:
                    continue
                teacher_affected_sections[teacher_id].add(sec_id)
                teacher_affected_subjects[teacher_id].add(subj_id)
                spw = int(getattr(subj, "sessions_per_week", 0) or 0)
                block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                if str(getattr(subj, "subject_type", "")).upper() == "LAB":
                    teacher_required_slots[teacher_id] += spw * max(block, 1)
                else:
                    teacher_required_slots[teacher_id] += spw

            # 2) Count combined THEORY groups once per group.
            for gid in sorted(list(combined_gids_seen), key=lambda x: str(x)):
                sec_ids = combined_group_sections.get(gid, [])
                if len(sec_ids) < 2:
                    continue
                subj_id = combined_group_subject.get(gid)
                subj = subj_by_id.get(subj_id)
                if subj is None or str(getattr(subj, "subject_type", "")) != "THEORY":
                    continue
                spw = int(getattr(subj, "sessions_per_week", 0) or 0)
                if spw <= 0:
                    continue

                teacher_id = combined_group_teacher.get(gid)
                if teacher_id is None:
                    # Legacy fallback: strict rule (all sections must have the same single teacher).
                    teacher_id = None
                    for sid in sec_ids:
                        teachers = teachers_by_section_subject.get((sid, subj_id), set())
                        if len(teachers) != 1:
                            teacher_id = None
                            break
                        tid = next(iter(teachers))
                        if teacher_id is None:
                            teacher_id = tid
                        elif teacher_id != tid:
                            teacher_id = None
                            break
                if teacher_id is None:
                    continue

                teacher_required_slots[teacher_id] += int(spw)
                for sid in sec_ids:
                    teacher_affected_sections[teacher_id].add(sid)
                teacher_affected_subjects[teacher_id].add(subj_id)

            for teacher_id, required in teacher_required_slots.items():
                teacher = teacher_by_id.get(teacher_id)
                if teacher is None:
                    continue
                if required > int(getattr(teacher, "max_per_week", 0) or 0):
                    max_per_week = int(getattr(teacher, "max_per_week", 0) or 0)
                    affected_section_ids = sorted([str(x) for x in teacher_affected_sections.get(teacher_id, set())])
                    affected_subject_ids = sorted([str(x) for x in teacher_affected_subjects.get(teacher_id, set())])
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="TEACHER_LOAD_EXCEEDS_MAX_PER_WEEK",
                            message="Assigned teaching load exceeds teacher.max_per_week; solve will be infeasible.",
                            teacher_id=teacher_id,
                            metadata={
                                "teacher_id": str(teacher_id),
                                "teacher_name": str(getattr(teacher, "full_name", "") or getattr(teacher, "code", "")),
                                "max_per_week": max_per_week,
                                "assigned_slots": int(required),
                                "difference": int(required) - max_per_week,
                                "affected_section_ids": affected_section_ids,
                                "affected_subject_ids": affected_subject_ids,
                            },
                        )
                    )

    # Fixed timetable entries (hard locks) validation.
    if section_ids:
        # Track locked occupied slot indices per (section, day) for the new
        # "max 3 empty slots between classes" hard constraint.
        # We only use this for a safe infeasibility pre-check: when two locked
        # events are far apart AND there is literally no allowed slot between
        # them (window/breaks), the gap is unavoidable.
        locked_indices_by_section_day: dict[tuple[Any, int], set[int]] = defaultdict(set)

        fixed_section_slot_pairs: set[tuple[Any, Any]] = set()
        fixed_rows: list[FixedTimetableEntry] = (
            db.execute(
                where_tenant(
                    select(FixedTimetableEntry)
                    .where(FixedTimetableEntry.section_id.in_(section_ids))
                    .where(FixedTimetableEntry.is_active.is_(True)),
                    FixedTimetableEntry,
                    tenant_id,
                )
            )
            .scalars()
            .all()
        )

        for fe in fixed_rows:
            fixed_section_slot_pairs.add((fe.section_id, fe.slot_id))

        if fixed_rows:
            fixed_subject_ids = {r.subject_id for r in fixed_rows}
            fixed_teacher_ids = {r.teacher_id for r in fixed_rows}
            fixed_room_ids = {r.room_id for r in fixed_rows}

            fixed_subjects = (
                db.execute(where_tenant(select(Subject).where(Subject.id.in_(list(fixed_subject_ids))), Subject, tenant_id))
                .scalars()
                .all()
            )
            fixed_teachers = (
                db.execute(where_tenant(select(Teacher).where(Teacher.id.in_(list(fixed_teacher_ids))), Teacher, tenant_id))
                .scalars()
                .all()
            )

            fixed_rooms = (
                db.execute(where_tenant(select(Room).where(Room.id.in_(list(fixed_room_ids))), Room, tenant_id))
                .scalars()
                .all()
            )

            fixed_subject_by_id = {s.id: s for s in fixed_subjects}
            fixed_teacher_by_id = {t.id: t for t in fixed_teachers}
            fixed_room_by_id = {r.id: r for r in fixed_rooms}

            # Precompute allowed subject ids per section (mapping override else track curriculum).
            allowed_subject_ids_by_section: dict[Any, set[Any]] = {}
            # Track rows by (academic_year_id, track)
            track_rows_all = (
                db.execute(where_tenant(select(TrackSubject).where(TrackSubject.program_id == program_id), TrackSubject, tenant_id))
                .scalars()
                .all()
            )
            track_by_year_track: dict[tuple[Any, str], list[TrackSubject]] = defaultdict(list)
            for r in track_rows_all:
                track_by_year_track[(r.academic_year_id, str(r.track))].append(r)

            for section in sections:
                mapped = mapped_subject_ids_by_section.get(section.id, [])
                if mapped:
                    allowed_subject_ids_by_section[section.id] = set(mapped)
                    continue

                effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
                rows = track_by_year_track.get((effective_year_id, str(section.track)), [])
                mandatory = [r for r in rows if not r.is_elective]
                allowed = {r.subject_id for r in mandatory}

                # Also allow elective-block subjects for this section.
                sec_block_ids = blocks_by_section.get(section.id, [])
                if sec_block_ids:
                    b_pairs = []
                    if block_ids:
                        for bid in sec_block_ids:
                            b_pairs.extend(block_subjects_by_block.get(bid, []))
                    for subj_id, _tid in b_pairs:
                        allowed.add(subj_id)
                allowed_subject_ids_by_section[section.id] = allowed

            # Assignment lookup for fixed entries: (section, subject) -> teacher_id
            assign_rows = (
                db.execute(
                    where_tenant(
                        select(
                            TeacherSubjectSection.section_id,
                            TeacherSubjectSection.subject_id,
                            TeacherSubjectSection.teacher_id,
                        )
                        .where(TeacherSubjectSection.section_id.in_(section_ids))
                        .where(TeacherSubjectSection.is_active.is_(True)),
                        TeacherSubjectSection,
                        tenant_id,
                    )
                )
                .all()
            )
            assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = {}
            dup_assigned: set[tuple[Any, Any]] = set()
            for sec_id, subj_id, teacher_id in assign_rows:
                key = (sec_id, subj_id)
                if key in assigned_teacher_by_section_subject and assigned_teacher_by_section_subject[key] != teacher_id:
                    dup_assigned.add(key)
                else:
                    assigned_teacher_by_section_subject[key] = teacher_id

            eligible_triplets: set[tuple[Any, Any, Any]] = set()
            for _sec_id, subj_id, teacher_id in assign_rows:
                subj = fixed_subject_by_id.get(subj_id)
                if subj is None:
                    continue
                eligible_triplets.add((teacher_id, subj_id, subj.academic_year_id))

            # Additional infeasibility checks for fixed locks
            fixed_teacher_slot_seen: dict[tuple[Any, Any], Any] = {}  # (teacher_id, slot_id) -> section_id

            for fe in fixed_rows:
                # Fixed entry must not overlap section breaks.
                if fe.slot_id in break_slot_ids_by_section.get(fe.section_id, set()):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_ON_SECTION_BREAK",
                            message="Fixed entry overlaps a section break; remove the break or move the fixed entry.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            room_id=fe.room_id,
                            slot_id=fe.slot_id,
                        )
                    )

                subj = fixed_subject_by_id.get(fe.subject_id)
                teacher = fixed_teacher_by_id.get(fe.teacher_id)
                room = fixed_room_by_id.get(fe.room_id)

                if subj is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_SUBJECT_NOT_FOUND",
                            message="Fixed entry references a subject that does not exist.",
                            section_id=fe.section_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )
                    continue

                if teacher is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_TEACHER_NOT_FOUND",
                            message="Fixed entry references a teacher that does not exist.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )
                    continue

                if room is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_ROOM_NOT_FOUND",
                            message="Fixed entry references a room that does not exist.",
                            section_id=fe.section_id,
                            room_id=fe.room_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )
                    continue

                if bool(getattr(room, "is_special", False)):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_ROOM_IS_SPECIAL",
                            message="Fixed entries cannot use special rooms. Use a Special Allotment lock instead.",
                            section_id=fe.section_id,
                            room_id=fe.room_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                            metadata={"room_code": str(getattr(room, "code", ""))},
                        )
                    )

                day_idx = slot_id_to_day_index.get(fe.slot_id)
                if day_idx is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_SLOT_NOT_FOUND",
                            message="Fixed entry references a time slot that does not exist.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )
                    continue

                d, si = day_idx

                # Mark locked occupancy for gap feasibility checks.
                locked_indices_by_section_day[(fe.section_id, int(d))].add(int(si))
                w = windows_by_section_day.get((fe.section_id, int(d)))
                if w is None or si < int(w.start_slot_index) or si > int(w.end_slot_index):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_SLOT_OUTSIDE_SECTION_WINDOW",
                            message="Fixed entry is outside the section's working window.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                            metadata={"day_of_week": int(d), "slot_index": int(si)},
                        )
                    )

                allowed_subj = allowed_subject_ids_by_section.get(fe.section_id, set())
                if allowed_subj and fe.subject_id not in allowed_subj:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_SUBJECT_NOT_ALLOWED_FOR_SECTION",
                            message="Fixed entry uses a subject that is not part of this section's curriculum/mapping.",
                            section_id=fe.section_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )

                # Fixed teacher must match the strict assignment.
                if (fe.section_id, fe.subject_id) in dup_assigned:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="DUPLICATE_TEACHER_ASSIGNMENT",
                            message="Multiple teachers are assigned for this section+subject; fixed entry cannot be validated.",
                            section_id=fe.section_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )
                else:
                    assigned_tid = assigned_teacher_by_section_subject.get((fe.section_id, fe.subject_id))
                    if assigned_tid is None:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="MISSING_TEACHER_ASSIGNMENT",
                                message="No teacher assigned for this section+subject; fixed entry cannot be satisfied.",
                                section_id=fe.section_id,
                                subject_id=fe.subject_id,
                                slot_id=fe.slot_id,
                            )
                        )
                    elif fe.teacher_id != assigned_tid:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="FIXED_TEACHER_MISMATCH_ASSIGNMENT",
                                message="Fixed entry teacher does not match the assigned teacher for this section+subject.",
                                section_id=fe.section_id,
                                subject_id=fe.subject_id,
                                teacher_id=fe.teacher_id,
                                slot_id=fe.slot_id,
                                metadata={"assigned_teacher_id": str(assigned_tid)},
                            )
                        )

                if teacher.weekly_off_day is not None and int(teacher.weekly_off_day) == int(d):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_TEACHER_WEEKLY_OFF_DAY",
                            message="Fixed entry schedules the teacher on their weekly off day.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                            metadata={"day_of_week": int(d)},
                        )
                    )

                if (fe.teacher_id, fe.subject_id, subj.academic_year_id) not in eligible_triplets:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_TEACHER_NOT_ELIGIBLE",
                            message="Fixed entry assigns a teacher who is not eligible for this subject/year.",
                            section_id=fe.section_id,
                            teacher_id=fe.teacher_id,
                            subject_id=fe.subject_id,
                            slot_id=fe.slot_id,
                        )
                    )

                # LAB: the fixed slot represents the LAB start; must fit contiguously.
                if str(subj.subject_type) == "LAB":
                    block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                    if block < 1:
                        block = 1
                    # Mark the entire LAB block as occupied.
                    for j in range(block):
                        if (int(d), int(si) + int(j)) in slot_id_by_day_index:
                            locked_indices_by_section_day[(fe.section_id, int(d))].add(int(si) + int(j))
                    end_idx = int(si) + block - 1
                    if w is None or end_idx > int(w.end_slot_index):
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="FIXED_LAB_BLOCK_DOES_NOT_FIT",
                                message="Fixed lab does not fit fully inside the section window as a contiguous block.",
                                section_id=fe.section_id,
                                teacher_id=fe.teacher_id,
                                subject_id=fe.subject_id,
                                slot_id=fe.slot_id,
                                metadata={"block_size": int(block), "start_slot_index": int(si)},
                            )
                        )
                    else:
                        valid_indices = slot_indices_by_day.get(int(d), set())
                        for j in range(block):
                            if int(si) + j not in valid_indices:
                                conflicts.append(
                                    ValidationConflict(
                                        conflict_type="FIXED_LAB_BLOCK_SLOT_MISSING",
                                        message="Fixed lab block references a missing time slot index.",
                                        section_id=fe.section_id,
                                        teacher_id=fe.teacher_id,
                                        subject_id=fe.subject_id,
                                        slot_id=fe.slot_id,
                                        metadata={"missing_slot_index": int(si) + j, "day_of_week": int(d)},
                                    )
                                )
                                break

                            # LAB block must not overlap breaks.
                            covered_slot_id = slot_id_by_day_index.get((int(d), int(si) + int(j)))
                            if covered_slot_id is not None and covered_slot_id in break_slot_ids_by_section.get(fe.section_id, set()):
                                conflicts.append(
                                    ValidationConflict(
                                        conflict_type="FIXED_LAB_OVERLAPS_BREAK",
                                        message="Fixed lab block overlaps a section break; move the fixed lab or adjust breaks.",
                                        section_id=fe.section_id,
                                        teacher_id=fe.teacher_id,
                                        subject_id=fe.subject_id,
                                        slot_id=fe.slot_id,
                                        metadata={"break_slot_id": str(covered_slot_id), "day_of_week": int(d), "slot_index": int(si) + int(j)},
                                    )
                                )
                                break

                # Fixed teacher overlap -> guaranteed infeasible.
                key = (fe.teacher_id, fe.slot_id)
                other_section = fixed_teacher_slot_seen.get(key)
                if other_section is not None and other_section != fe.section_id:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="FIXED_TEACHER_OVERLAP",
                            message="Two fixed entries assign the same teacher in the same time slot across sections; model will be infeasible.",
                            teacher_id=fe.teacher_id,
                            slot_id=fe.slot_id,
                            metadata={"section_ids": [str(other_section), str(fe.section_id)]},
                        )
                    )
                else:
                    fixed_teacher_slot_seen[key] = fe.section_id

    # Special allotments (hard locked events) validation.
    if section_ids:
        special_rows: list[SpecialAllotment] = (
            db.execute(
                where_tenant(
                    select(SpecialAllotment)
                    .where(SpecialAllotment.section_id.in_(section_ids))
                    .where(SpecialAllotment.is_active.is_(True)),
                    SpecialAllotment,
                    tenant_id,
                )
            )
            .scalars()
            .all()
        )

        if special_rows:
            special_subject_ids = {r.subject_id for r in special_rows}
            special_teacher_ids = {r.teacher_id for r in special_rows}
            special_room_ids = {r.room_id for r in special_rows if r.room_id is not None}

            special_subjects = (
                db.execute(where_tenant(select(Subject).where(Subject.id.in_(list(special_subject_ids))), Subject, tenant_id))
                .scalars()
                .all()
            )
            special_teachers = (
                db.execute(where_tenant(select(Teacher).where(Teacher.id.in_(list(special_teacher_ids))), Teacher, tenant_id))
                .scalars()
                .all()
            )

            special_rooms = (
                db.execute(where_tenant(select(Room).where(Room.id.in_(list(special_room_ids))), Room, tenant_id))
                .scalars()
                .all()
            )

            special_subject_by_id = {s.id: s for s in special_subjects}
            special_teacher_by_id = {t.id: t for t in special_teachers}
            special_room_by_id = {r.id: r for r in special_rooms}

            # Precompute allowed subject ids per section (mapping override else track curriculum).
            allowed_subject_ids_by_section: dict[Any, set[Any]] = {}

            track_rows_all = (
                db.execute(where_tenant(select(TrackSubject).where(TrackSubject.program_id == program_id), TrackSubject, tenant_id))
                .scalars()
                .all()
            )
            track_by_year_track: dict[tuple[Any, str], list[TrackSubject]] = defaultdict(list)
            for r in track_rows_all:
                track_by_year_track[(r.academic_year_id, str(r.track))].append(r)

            for section in sections:
                mapped = mapped_subject_ids_by_section.get(section.id, [])
                if mapped:
                    allowed_subject_ids_by_section[section.id] = set(mapped)
                    continue

                effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
                rows = track_by_year_track.get((effective_year_id, str(section.track)), [])
                mandatory = [r for r in rows if not r.is_elective]
                allowed = {r.subject_id for r in mandatory}

                # Also allow elective-block subjects for this section.
                sec_block_ids = blocks_by_section.get(section.id, [])
                if sec_block_ids:
                    b_pairs = []
                    if block_ids:
                        for bid in sec_block_ids:
                            b_pairs.extend(block_subjects_by_block.get(bid, []))
                    for subj_id, _tid in b_pairs:
                        allowed.add(subj_id)
                allowed_subject_ids_by_section[section.id] = allowed

            # Assignment lookup: (section, subject) -> teacher_id
            assign_rows = (
                db.execute(
                    where_tenant(
                        select(
                            TeacherSubjectSection.section_id,
                            TeacherSubjectSection.subject_id,
                            TeacherSubjectSection.teacher_id,
                        )
                        .where(TeacherSubjectSection.section_id.in_(section_ids))
                        .where(TeacherSubjectSection.is_active.is_(True)),
                        TeacherSubjectSection,
                        tenant_id,
                    )
                )
                .all()
            )
            assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = {}
            dup_assigned: set[tuple[Any, Any]] = set()
            for sec_id, subj_id, teacher_id in assign_rows:
                key = (sec_id, subj_id)
                if key in assigned_teacher_by_section_subject and assigned_teacher_by_section_subject[key] != teacher_id:
                    dup_assigned.add(key)
                else:
                    assigned_teacher_by_section_subject[key] = teacher_id

            special_teacher_slot_seen: dict[tuple[Any, Any], Any] = {}
            special_room_slot_seen: dict[tuple[Any, Any], Any] = {}

            for sa in special_rows:
                # Special allotment must not overlap section breaks.
                if sa.slot_id in break_slot_ids_by_section.get(sa.section_id, set()):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_ON_SECTION_BREAK",
                            message="Special allotment overlaps a section break; remove the break or move the special allotment.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            room_id=sa.room_id,
                            slot_id=sa.slot_id,
                        )
                    )

                subj = special_subject_by_id.get(sa.subject_id)
                teacher = special_teacher_by_id.get(sa.teacher_id)
                if sa.room_id is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_ROOM_MISSING",
                            message="Special allotment must specify a room.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                    continue

                room = special_room_by_id.get(sa.room_id)
                if room is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_ROOM_NOT_FOUND",
                            message="Special allotment references a room that does not exist.",
                            section_id=sa.section_id,
                            room_id=sa.room_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                    continue

                # For LAB special allotments, the entire block must not overlap breaks.
                if subj is not None and str(subj.subject_type) == "LAB":
                    di = slot_id_to_day_index.get(sa.slot_id)
                    if di is not None:
                        d, si = int(di[0]), int(di[1])
                        block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                        if block < 1:
                            block = 1
                        for j in range(block):
                            covered_slot_id = slot_id_by_day_index.get((int(d), int(si) + int(j)))
                            if covered_slot_id is not None and covered_slot_id in break_slot_ids_by_section.get(sa.section_id, set()):
                                conflicts.append(
                                    ValidationConflict(
                                        conflict_type="SPECIAL_LAB_OVERLAPS_BREAK",
                                        message="Special lab block overlaps a section break; move the special allotment or adjust breaks.",
                                        section_id=sa.section_id,
                                        teacher_id=sa.teacher_id,
                                        subject_id=sa.subject_id,
                                        room_id=sa.room_id,
                                        slot_id=sa.slot_id,
                                        metadata={"break_slot_id": str(covered_slot_id), "day_of_week": int(d), "slot_index": int(si) + int(j)},
                                    )
                                )
                                break

                if not bool(getattr(room, "is_special", False)):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_ROOM_NOT_SPECIAL",
                            message="Special allotments must use rooms marked as special.",
                            section_id=sa.section_id,
                            room_id=sa.room_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                            metadata={"room_code": str(getattr(room, "code", ""))},
                        )
                    )

                if subj is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_SUBJECT_NOT_FOUND",
                            message="Special allotment references a subject that does not exist.",
                            section_id=sa.section_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                    continue

                if teacher is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_TEACHER_NOT_FOUND",
                            message="Special allotment references a teacher that does not exist.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                    continue

                day_idx = slot_id_to_day_index.get(sa.slot_id)
                if day_idx is None:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_SLOT_NOT_FOUND",
                            message="Special allotment references a time slot that does not exist.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                    continue

                d, si = day_idx

                # Mark locked occupancy for gap feasibility checks.
                locked_indices_by_section_day[(sa.section_id, int(d))].add(int(si))
                w = windows_by_section_day.get((sa.section_id, int(d)))
                if w is None or si < int(w.start_slot_index) or si > int(w.end_slot_index):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_SLOT_OUTSIDE_SECTION_WINDOW",
                            message="Special allotment is outside the section's working window.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                            metadata={"day_of_week": int(d), "slot_index": int(si)},
                        )
                    )

                allowed_subj = allowed_subject_ids_by_section.get(sa.section_id, set())
                if allowed_subj and sa.subject_id not in allowed_subj:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_SUBJECT_NOT_ALLOWED_FOR_SECTION",
                            message="Special allotment uses a subject that is not part of this section's curriculum/mapping.",
                            section_id=sa.section_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )

                # Special teacher must match the strict assignment.
                if (sa.section_id, sa.subject_id) in dup_assigned:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="DUPLICATE_TEACHER_ASSIGNMENT",
                            message="Multiple teachers are assigned for this section+subject; special allotment cannot be validated.",
                            section_id=sa.section_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )
                else:
                    assigned_tid = assigned_teacher_by_section_subject.get((sa.section_id, sa.subject_id))
                    if assigned_tid is None:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="MISSING_TEACHER_ASSIGNMENT",
                                message="No teacher assigned for this section+subject; special allotment cannot be satisfied.",
                                section_id=sa.section_id,
                                subject_id=sa.subject_id,
                                slot_id=sa.slot_id,
                            )
                        )
                    elif sa.teacher_id != assigned_tid:
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="SPECIAL_TEACHER_MISMATCH_ASSIGNMENT",
                                message="Special allotment teacher does not match the assigned teacher for this section+subject.",
                                section_id=sa.section_id,
                                subject_id=sa.subject_id,
                                teacher_id=sa.teacher_id,
                                slot_id=sa.slot_id,
                                metadata={"assigned_teacher_id": str(assigned_tid)},
                            )
                        )

                if teacher.weekly_off_day is not None and int(teacher.weekly_off_day) == int(d):
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_TEACHER_WEEKLY_OFF_DAY",
                            message="Special allotment schedules the teacher on their weekly off day.",
                            section_id=sa.section_id,
                            teacher_id=sa.teacher_id,
                            subject_id=sa.subject_id,
                            room_id=sa.room_id,
                            slot_id=sa.slot_id,
                            metadata={
                                "teacher_id": str(sa.teacher_id),
                                "teacher_name": str(getattr(teacher, "full_name", "") or getattr(teacher, "code", "")),
                                "weekly_off_day": int(teacher.weekly_off_day),
                                "locked_day": int(d),
                                "locked_slot_index": int(si),
                                "section_id": str(sa.section_id),
                                "subject_id": str(sa.subject_id),
                                "room_id": str(sa.room_id) if sa.room_id is not None else None,
                            },
                        )
                    )

                # LAB: slot represents LAB start; must fit contiguously.
                if str(subj.subject_type) == "LAB":
                    block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                    if block < 1:
                        block = 1
                    # Mark the entire LAB block as occupied.
                    for j in range(block):
                        if (int(d), int(si) + int(j)) in slot_id_by_day_index:
                            locked_indices_by_section_day[(sa.section_id, int(d))].add(int(si) + int(j))
                    end_idx = int(si) + block - 1
                    if w is None or end_idx > int(w.end_slot_index):
                        conflicts.append(
                            ValidationConflict(
                                conflict_type="SPECIAL_LAB_BLOCK_DOES_NOT_FIT",
                                message="Special lab does not fit fully inside the section window as a contiguous block.",
                                section_id=sa.section_id,
                                teacher_id=sa.teacher_id,
                                subject_id=sa.subject_id,
                                slot_id=sa.slot_id,
                                metadata={"block_size": int(block), "start_slot_index": int(si)},
                            )
                        )
                    else:
                        valid_indices = slot_indices_by_day.get(int(d), set())
                        for j in range(block):
                            if int(si) + j not in valid_indices:
                                conflicts.append(
                                    ValidationConflict(
                                        conflict_type="SPECIAL_LAB_BLOCK_SLOT_MISSING",
                                        message="Special lab block references a missing time slot index.",
                                        section_id=sa.section_id,
                                        teacher_id=sa.teacher_id,
                                        subject_id=sa.subject_id,
                                        slot_id=sa.slot_id,
                                        metadata={"missing_slot_index": int(si) + j, "day_of_week": int(d)},
                                    )
                                )
                                break

                # Fixed + special cannot occupy the same section/slot.
                if (sa.section_id, sa.slot_id) in fixed_section_slot_pairs:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_CONFLICTS_WITH_FIXED_ENTRY",
                            message="Special allotment conflicts with an existing fixed entry in the same section/slot.",
                            section_id=sa.section_id,
                            subject_id=sa.subject_id,
                            slot_id=sa.slot_id,
                        )
                    )

                # Special teacher overlap -> guaranteed infeasible.
                key = (sa.teacher_id, sa.slot_id)
                other_section = special_teacher_slot_seen.get(key)
                if other_section is not None and other_section != sa.section_id:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_TEACHER_OVERLAP",
                            message="Two special allotments assign the same teacher in the same time slot across sections; model will be infeasible.",
                            teacher_id=sa.teacher_id,
                            slot_id=sa.slot_id,
                            metadata={"section_ids": [str(other_section), str(sa.section_id)]},
                        )
                    )
                else:
                    special_teacher_slot_seen[key] = sa.section_id

                # Special room overlap -> guaranteed infeasible for locked rooms.
                rkey = (sa.room_id, sa.slot_id)
                other_section = special_room_slot_seen.get(rkey)
                if other_section is not None and other_section != sa.section_id:
                    conflicts.append(
                        ValidationConflict(
                            conflict_type="SPECIAL_ROOM_OVERLAP",
                            message="Two special allotments assign the same room in the same time slot across sections; locked rooms cannot overlap.",
                            room_id=sa.room_id,
                            slot_id=sa.slot_id,
                            metadata={"section_ids": [str(other_section), str(sa.section_id)]},
                        )
                    )
                else:
                    special_room_slot_seen[rkey] = sa.section_id

    # ------------------------------------------------------------------
    # Pre-solve feasibility check for the new section max-gap constraint.
    # ------------------------------------------------------------------
    # We only flag cases that are guaranteed infeasible:
    # If there are two locked occupied slots with >3 empty slots between them,
    # AND there is no allowed slot_index between them (due to window/breaks),
    # then the solver cannot insert any class to break the gap.
    if section_ids:
        MAX_EMPTY_GAP_SLOTS = 3
        min_dist = MAX_EMPTY_GAP_SLOTS + 2  # distance >= 5 implies 4+ empty slots between
        for section in sections:
            for d in active_days:
                w = windows_by_section_day.get((section.id, int(d)))
                if w is None:
                    continue
                window_len = int(w.end_slot_index) - int(w.start_slot_index) + 1
                if window_len < (MAX_EMPTY_GAP_SLOTS + 3):
                    continue

                # Compute allowed indices inside the section window excluding breaks.
                allowed = set(range(int(w.start_slot_index), int(w.end_slot_index) + 1))
                for bid in break_slot_ids_by_section.get(section.id, set()):
                    di = slot_id_to_day_index.get(bid)
                    if di is None:
                        continue
                    bd, bsi = int(di[0]), int(di[1])
                    if bd == int(d):
                        allowed.discard(int(bsi))

                occ = sorted(locked_indices_by_section_day.get((section.id, int(d)), set()))
                if len(occ) < 2:
                    continue

                for i in range(0, len(occ)):
                    for j in range(i + 1, len(occ)):
                        if int(occ[j]) - int(occ[i]) < min_dist:
                            continue
                        has_insertable = False
                        for k in range(int(occ[i]) + 1, int(occ[j])):
                            if k in allowed:
                                has_insertable = True
                                break
                        if not has_insertable:
                            conflicts.append(
                                ValidationConflict(
                                    conflict_type="UNAVOIDABLE_SECTION_GAP_EXCEEDS_3_EMPTY_SLOTS",
                                    message="Locked classes create an unavoidable gap > 3 empty slots; adjust windows/breaks or move locks.",
                                    section_id=section.id,
                                    metadata={
                                        "day_of_week": int(d),
                                        "locked_slot_index_a": int(occ[i]),
                                        "locked_slot_index_b": int(occ[j]),
                                        "max_empty_gap": int(MAX_EMPTY_GAP_SLOTS),
                                    },
                                )
                            )
                            break
                    else:
                        continue
                    break

    # Teacher capacity validation (strict mode helper): ensure total required weekly load can be covered.
    # We estimate load in *slots* per week: LAB counts as lab_block_size_slots per session.
    # This prevents wasting solver time when eligibility is too sparse.
    q_subjects = select(Subject).where(Subject.program_id == program_id).where(Subject.is_active.is_(True))
    if solve_year_ids:
        q_subjects = q_subjects.where(Subject.academic_year_id.in_(solve_year_ids))

    subject_by_id = {s.id: s for s in db.execute(q_subjects).scalars().all()}

    # Required subjects per section (track curriculum + electives)
    required_slots_by_subject = defaultdict(int)
    for section in sections:
        mapped = mapped_subject_ids_by_section.get(section.id, [])
        if mapped:
            section_weekly_load = 0
            valid_mapped_subjects = 0
            for subject_id in mapped:
                subj = subject_by_id.get(subject_id)
                if subj is None:
                    continue
                valid_mapped_subjects += 1
                sessions = int(subj.sessions_per_week)
                block = int(subj.lab_block_size_slots) if str(subj.subject_type) == "LAB" else 1
                required_slots_by_subject[subj.id] += sessions * block
                section_weekly_load += sessions * block

            if valid_mapped_subjects <= 0:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="MISSING_SECTION_SUBJECTS",
                        message="Section has subject mappings but none are valid active subjects for this solve scope.",
                        section_id=section.id,
                    )
                )
                continue

            if section_weekly_load > 30:
                conflicts.append(
                    ValidationConflict(
                        conflict_type="SECTION_WEEKLY_LOAD_GT_30",
                        message="Mapped subjects exceed 30 total weekly load slots.",
                        severity="WARN",
                        section_id=section.id,
                        metadata={"weekly_load_slots": int(section_weekly_load)},
                    )
                )

            continue

        effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
        track_rows = (
            db.execute(
                where_tenant(
                    select(TrackSubject)
                    .where(TrackSubject.program_id == program_id)
                    .where(TrackSubject.academic_year_id == effective_year_id)
                    .where(TrackSubject.track == section.track),
                    TrackSubject,
                    tenant_id,
                )
            )
            .scalars()
            .all()
        )
        mandatory_rows = [r for r in track_rows if not r.is_elective]
        sec_block_ids = blocks_by_section.get(section.id, [])

        any_subject = False

        # Mandatory
        for r in mandatory_rows:
            subj = subject_by_id.get(r.subject_id)
            if subj is None:
                continue
            any_subject = True
            sessions = r.sessions_override if r.sessions_override is not None else subj.sessions_per_week
            if str(subj.subject_type) == "LAB":
                required_slots_by_subject[subj.id] += int(sessions) * int(subj.lab_block_size_slots)
            else:
                required_slots_by_subject[subj.id] += int(sessions)

        # Elective blocks: section load is one slot per block occurrence (shared across parallel electives).
        # We estimate required slots based on sessions_per_week of subjects in the block.
        if sec_block_ids:
            for bid in sec_block_ids:
                pairs = block_subjects_by_block.get(bid, [])
                if not pairs:
                    continue
                subj = subject_by_id.get(pairs[0][0])
                if subj is None:
                    continue
                any_subject = True
                required_slots_by_subject[subj.id] += int(getattr(subj, "sessions_per_week", 0) or 0)

        if not any_subject:
            conflicts.append(
                ValidationConflict(
                    conflict_type="MISSING_SECTION_SUBJECTS",
                    message="Section has no subjects (no section_subjects mapping and no track_subjects curriculum).",
                    section_id=section.id,
                )
            )

    # With strict (teacher, subject, section) assignments, capacity feasibility is validated
    # via per-teacher required load checks earlier.

    # Section weekly load must fit in the section's allowed window capacity.
    # This is a necessary (not sufficient) feasibility check.
    slot_by_day_index: dict[tuple[int, int], Any] = {(int(d), int(i)): sid for d, i, sid in slot_rows}
    allowed_slots_by_section = defaultdict(set)
    for w in windows:
        for si in range(int(w.start_slot_index), int(w.end_slot_index) + 1):
            sid = slot_by_day_index.get((int(w.day_of_week), int(si)))
            if sid is not None:
                allowed_slots_by_section[w.section_id].add(sid)

    for section in sections:
        allowed = allowed_slots_by_section.get(section.id, set())
        if not allowed:
            # Missing window is already handled above.
            continue

        # Estimate required slot load for this section.
        mapped = mapped_subject_ids_by_section.get(section.id, [])
        required_slots = 0
        if mapped:
            for subject_id in mapped:
                subj = subject_by_id.get(subject_id)
                if subj is None:
                    continue
                sessions = int(subj.sessions_per_week)
                block = int(subj.lab_block_size_slots) if str(subj.subject_type) == "LAB" else 1
                required_slots += sessions * block
        else:
            effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
            track_rows = (
                db.execute(
                    where_tenant(
                        select(TrackSubject)
                        .where(TrackSubject.program_id == program_id)
                        .where(TrackSubject.academic_year_id == effective_year_id)
                        .where(TrackSubject.track == section.track),
                        TrackSubject,
                        tenant_id,
                    )
                )
                .scalars()
                .all()
            )
            mandatory_rows = [r for r in track_rows if not r.is_elective]
            for r in mandatory_rows:
                subj = subject_by_id.get(r.subject_id)
                if subj is None:
                    continue
                sessions = r.sessions_override if r.sessions_override is not None else subj.sessions_per_week
                block = int(subj.lab_block_size_slots) if str(subj.subject_type) == "LAB" else 1
                required_slots += int(sessions) * block

            # Add elective block load: one slot per block occurrence.
            sec_block_ids = blocks_by_section.get(section.id, [])
            if sec_block_ids:
                for bid in sec_block_ids:
                    pairs = block_subjects_by_block.get(bid, [])
                    if not pairs:
                        continue
                    subj = subject_by_id.get(pairs[0][0])
                    if subj is None:
                        continue
                    required_slots += int(getattr(subj, "sessions_per_week", 0) or 0)

        if required_slots > len(allowed):
            conflicts.append(
                ValidationConflict(
                    conflict_type="SECTION_LOAD_EXCEEDS_WINDOW_CAPACITY",
                    message="Section weekly required load exceeds the number of allowed time slots in its working windows.",
                    section_id=section.id,
                    metadata={"required_slots": int(required_slots), "allowed_slots": int(len(allowed))},
                )
            )

    # =========================
    # Combined Groups (v2 + legacy fallback)
    # =========================
    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")

    if use_v2:
        q_combined = (
            select(CombinedGroup, Subject, Teacher, CombinedGroupSection.section_id)
            .join(Subject, Subject.id == CombinedGroup.subject_id)
            .outerjoin(Teacher, Teacher.id == CombinedGroup.teacher_id)
            .join(CombinedGroupSection, CombinedGroupSection.combined_group_id == CombinedGroup.id)
            .where(Subject.program_id == program_id)
            .where(Subject.is_active.is_(True))
        )
        if solve_year_ids:
            q_combined = q_combined.where(CombinedGroup.academic_year_id.in_(solve_year_ids)).where(
                Subject.academic_year_id.in_(solve_year_ids)
            )
        q_combined = where_tenant(q_combined, CombinedGroup, tenant_id)
        q_combined = where_tenant(q_combined, CombinedGroupSection, tenant_id)
        q_combined = where_tenant(q_combined, Subject, tenant_id)
        combined_rows = db.execute(q_combined).all()
    else:
        q_combined = (
            select(CombinedSubjectGroup, Subject, CombinedSubjectSection.section_id)
            .join(Subject, Subject.id == CombinedSubjectGroup.subject_id)
            .join(CombinedSubjectSection, CombinedSubjectSection.combined_group_id == CombinedSubjectGroup.id)
            .where(Subject.program_id == program_id)
            .where(Subject.is_active.is_(True))
        )
        if solve_year_ids:
            q_combined = q_combined.where(CombinedSubjectGroup.academic_year_id.in_(solve_year_ids)).where(
                Subject.academic_year_id.in_(solve_year_ids)
            )
        q_combined = where_tenant(q_combined, CombinedSubjectGroup, tenant_id)
        q_combined = where_tenant(q_combined, CombinedSubjectSection, tenant_id)
        q_combined = where_tenant(q_combined, Subject, tenant_id)
        legacy_rows = db.execute(q_combined).all()
        combined_rows = [(g, subj, None, sec_id) for g, subj, sec_id in legacy_rows]

    if combined_rows:
        has_lt = (
            db.execute(
                where_tenant(
                    select(Room.id).where(Room.is_active.is_(True)).where(Room.room_type == "LT").limit(1),
                    Room,
                    tenant_id,
                )
            ).first()
            is not None
        )
        if not has_lt:
            conflicts.append(
                ValidationConflict(
                    conflict_type="MISSING_LT_ROOMS_FOR_COMBINED",
                    message="Combined classes require at least one active LT room.",
                )
            )

    group_sections = defaultdict(set)
    group_subject = {}
    group_subject_code = {}
    group_teacher_id = {}
    group_teacher = {}
    for g, subj, teacher, sec_id in combined_rows:
        group_sections[g.id].add(sec_id)
        group_subject[g.id] = subj.id
        group_subject_code[g.id] = str(subj.code)
        group_teacher_id[g.id] = getattr(g, "teacher_id", None)
        if g.id not in group_teacher:
            group_teacher[g.id] = teacher

    section_by_id = {s.id: s for s in sections}
    solve_section_ids = set(section_by_id.keys())

    # Preload track rows and elective selections to check subject existence and sessions/week.
    track_rows_all = (
        db.execute(
            where_tenant(
                select(TrackSubject).where(TrackSubject.program_id == program_id),
                TrackSubject,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )
    track_rows_by_track_year = defaultdict(list)
    for r in track_rows_all:
        track_rows_by_track_year[(str(r.track), r.academic_year_id)].append(r)

    def required_sessions_for_section_subject(section, subj_id):
        mapped = mapped_subject_ids_by_section.get(section.id, [])
        subj = subject_by_id.get(subj_id)
        if subj is None:
            return None

        if mapped:
            return int(subj.sessions_per_week) if subj_id in set(mapped) else None

        effective_year_id = academic_year_id if academic_year_id is not None else section.academic_year_id
        rows = track_rows_by_track_year.get((str(section.track), effective_year_id), [])
        mandatory = [r for r in rows if not r.is_elective]
        elective = [r for r in rows if r.is_elective]

        for r in mandatory:
            if r.subject_id == subj_id:
                sessions = r.sessions_override if r.sessions_override is not None else subj.sessions_per_week
                return int(sessions or 0)

        # Elective blocks: treat any block subject as present in the mapped sections.
        sec_block_ids = blocks_by_section.get(section.id, [])
        if sec_block_ids:
            for bid in sec_block_ids:
                pairs = block_subjects_by_block.get(bid, [])
                if any(sid == subj_id for sid, _tid in pairs):
                    return int(subj.sessions_per_week)

        return None

    for gid, sec_ids in group_sections.items():
        subj_id = group_subject.get(gid)
        subj_code = group_subject_code.get(gid, "")
        if subj_id is None:
            continue
        subj = subject_by_id.get(subj_id)
        if subj is None:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_SUBJECT_NOT_IN_SOLVE_SCOPE",
                    message="Combined group subject is not an active subject in this solve scope.",
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )
            continue
        if str(subj.subject_type) != "THEORY":
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_SUBJECT_NOT_THEORY",
                    message="Combined groups are allowed only for THEORY subjects.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )

        tid = group_teacher_id.get(gid)
        t = group_teacher.get(gid)

        # If v2 teacher isn't present (or we're on legacy tables), infer teacher via strict assignments.
        mismatch = False
        if tid is None:
            inferred_tid = None
            for sid in sec_ids:
                assigned_tid = assigned_teacher_by_section_subject.get((sid, subj_id))
                if assigned_tid is None:
                    inferred_tid = None
                    break
                if inferred_tid is None:
                    inferred_tid = assigned_tid
                elif inferred_tid != assigned_tid:
                    mismatch = True
                    inferred_tid = None
                    break
            tid = inferred_tid

        if mismatch:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_TEACHER_MISMATCH",
                    message="Combined group requires a single shared teacher across sections.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )

        if tid is not None and t is None:
            t = (
                db.execute(where_tenant(select(Teacher).where(Teacher.id == tid), Teacher, tenant_id)).scalars().first()
            )
        if tid is None or t is None or not bool(getattr(t, "is_active", False)):
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_TEACHER_MISSING",
                    message="Combined group must have an active teacher assigned.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )

        if len(sec_ids) < 2:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_TOO_SMALL",
                    message="Combined group must contain at least 2 sections.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )
            continue

        # Intersection of allowed slots must be non-empty for combined groups.
        allowed_intersection = None
        for sid in sec_ids:
            s_allowed = set(allowed_slots_by_section.get(sid, set()))
            allowed_intersection = s_allowed if allowed_intersection is None else (allowed_intersection & s_allowed)
        if not allowed_intersection:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_NO_COMMON_SLOTS",
                    message="Combined group has no common allowed time slots across its sections' working windows.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code},
                )
            )
            continue

        missing_sections = [str(sid) for sid in sec_ids if sid not in solve_section_ids]
        if missing_sections:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_SECTION_NOT_IN_SOLVE",
                    message="Combined group contains sections not present in this solve (inactive or different academic year).",
                    subject_id=subj_id,
                    metadata={
                        "combined_group_id": str(gid),
                        "subject_code": subj_code,
                        "missing_section_ids": missing_sections,
                    },
                )
            )
            continue

        sessions_list = []
        missing_in_sections = []
        for sid in sec_ids:
            section = section_by_id.get(sid)
            if section is None:
                continue
            val = required_sessions_for_section_subject(section, subj_id)
            if val is None:
                missing_in_sections.append(getattr(section, "code", str(sid)))
            else:
                sessions_list.append(int(val))

        if missing_in_sections:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_SUBJECT_NOT_IN_ALL_SECTIONS",
                    message="Combined group subject must exist in all selected sections.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code, "sections": missing_in_sections},
                )
            )
            continue

        if len(set(sessions_list)) > 1:
            conflicts.append(
                ValidationConflict(
                    conflict_type="COMBINED_GROUP_SESSIONS_MISMATCH",
                    message="Combined group requires the same sessions/week across all selected sections.",
                    subject_id=subj_id,
                    metadata={"combined_group_id": str(gid), "subject_code": subj_code, "values": sessions_list},
                )
            )

    persist_conflicts(db, run=run, conflicts=conflicts)
    return conflicts

