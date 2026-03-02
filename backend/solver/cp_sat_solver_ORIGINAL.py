from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy import delete, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.tenant import where_tenant
from core.db import table_exists
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.combined_subject_group import CombinedSubjectGroup
from models.combined_subject_section import CombinedSubjectSection
from models.room import Room
from models.section import Section
from models.elective_block import ElectiveBlock
from models.elective_block_subject import ElectiveBlockSubject
from models.section_elective_block import SectionElectiveBlock
from models.section_break import SectionBreak
from models.section_time_window import SectionTimeWindow
from models.section_subject import SectionSubject
from models.subject import Subject
from models.teacher import Teacher
from models.teacher_subject_section import TeacherSubjectSection
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun
from models.time_slot import TimeSlot
from models.track_subject import TrackSubject
from models.fixed_timetable_entry import FixedTimetableEntry
from models.special_allotment import SpecialAllotment

from core.config import settings


class SolverInvariantError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SolveResult:
    def __init__(
        self,
        *,
        status: str,
        entries_written: int,
        conflicts: list[TimetableConflict],
        diagnostics: list[dict] | None = None,
        reason_summary: str | None = None,
        objective_score: int | None = None,
        warnings: list[str] | None = None,
        solver_stats: dict | None = None,
    ):
        self.status = status
        self.entries_written = entries_written
        self.conflicts = conflicts
        self.diagnostics = diagnostics or []
        self.reason_summary = reason_summary
        self.objective_score = objective_score
        self.warnings = warnings or []
        self.solver_stats = solver_stats or {}


def solve_program_year(
    db: Session,
    *,
    run: TimetableRun,
    program_id,
    academic_year_id,
    seed: int | None,
    max_time_seconds: float,
    enforce_teacher_load_limits: bool = True,
    require_optimal: bool = False,
) -> SolveResult:
    return _solve_program(
        db,
        run=run,
        program_id=program_id,
        academic_year_id=academic_year_id,
        seed=seed,
        max_time_seconds=max_time_seconds,
        enforce_teacher_load_limits=enforce_teacher_load_limits,
        require_optimal=require_optimal,
    )


def solve_program_global(
    db: Session,
    *,
    run: TimetableRun,
    program_id,
    seed: int | None,
    max_time_seconds: float,
    enforce_teacher_load_limits: bool = True,
    require_optimal: bool = False,
) -> SolveResult:
    """Program-wide solve.

    Schedules all active sections for the program across all academic years in a single CP-SAT model.
    """
    return _solve_program(
        db,
        run=run,
        program_id=program_id,
        academic_year_id=None,
        seed=seed,
        max_time_seconds=max_time_seconds,
        enforce_teacher_load_limits=enforce_teacher_load_limits,
        require_optimal=require_optimal,
    )


def _solve_program(
    db: Session,
    *,
    run: TimetableRun,
    program_id,
    academic_year_id,
    seed: int | None,
    max_time_seconds: float,
    enforce_teacher_load_limits: bool,
    require_optimal: bool,
) -> SolveResult:
    tenant_id = getattr(run, "tenant_id", None)

    q_sections = select(Section).where(Section.program_id == program_id).where(Section.is_active.is_(True))
    q_sections = where_tenant(q_sections, Section, tenant_id)
    if academic_year_id is not None:
        q_sections = q_sections.where(Section.academic_year_id == academic_year_id)
    # else: program-wide solve (all academic years).

    sections: list[Section] = db.execute(q_sections.order_by(Section.code)).scalars().all()
    section_year_by_id = {s.id: s.academic_year_id for s in sections}
    solve_year_ids = sorted({s.academic_year_id for s in sections})

    slots: list[TimeSlot] = db.execute(where_tenant(select(TimeSlot), TimeSlot, tenant_id)).scalars().all()
    slot_by_day_index: dict[tuple[int, int], TimeSlot] = {(s.day_of_week, s.slot_index): s for s in slots}
    slot_info = {s.id: (s.day_of_week, s.slot_index) for s in slots}
    slots_by_day = defaultdict(list)
    for s in slots:
        slots_by_day[s.day_of_week].append(s)
    for d in slots_by_day:
        slots_by_day[d].sort(key=lambda x: x.slot_index)

    q_windows = select(SectionTimeWindow).where(SectionTimeWindow.section_id.in_([s.id for s in sections]))
    q_windows = where_tenant(q_windows, SectionTimeWindow, tenant_id)
    windows = db.execute(q_windows).scalars().all()
    windows_by_section = defaultdict(list)
    for w in windows:
        windows_by_section[w.section_id].append(w)

    # Fetch all active rooms (including special) so we can reason about special-allotment locks.
    q_rooms = where_tenant(select(Room).where(Room.is_active.is_(True)), Room, tenant_id)
    rooms_all: list[Room] = db.execute(q_rooms).scalars().all()
    room_by_id = {r.id: r for r in rooms_all}

    # Room pool for auto-assignment: NEVER include special rooms.
    rooms_by_type = defaultdict(list)
    for r in rooms_all:
        if bool(getattr(r, "is_special", False)):
            continue
        rooms_by_type[str(r.room_type)].append(r)

    q_subjects = select(Subject).where(Subject.program_id == program_id).where(Subject.is_active.is_(True))
    if solve_year_ids:
        q_subjects = q_subjects.where(Subject.academic_year_id.in_(solve_year_ids))
    q_subjects = where_tenant(q_subjects, Subject, tenant_id)
    subjects: list[Subject] = db.execute(q_subjects).scalars().all()
    subject_by_id = {s.id: s for s in subjects}

    q_teachers = where_tenant(select(Teacher).where(Teacher.is_active.is_(True)), Teacher, tenant_id)
    teachers: list[Teacher] = db.execute(q_teachers).scalars().all()
    teacher_by_id = {t.id: t for t in teachers}

    # Strict teacher assignment: (section_id, subject_id) -> teacher_id
    assigned_teacher_by_section_subject: dict[tuple[str, str], str] = {}
    if sections:
        rows = db.execute(
            where_tenant(
                select(
                    TeacherSubjectSection.section_id,
                    TeacherSubjectSection.subject_id,
                    TeacherSubjectSection.teacher_id,
                )
                .where(TeacherSubjectSection.section_id.in_([s.id for s in sections]))
                .where(TeacherSubjectSection.is_active.is_(True)),
                TeacherSubjectSection,
                tenant_id,
            )
        ).all()
        for sec_id, subj_id, teacher_id in rows:
            # If duplicates exist, validation should have caught it; keep a stable choice.
            assigned_teacher_by_section_subject.setdefault((sec_id, subj_id), teacher_id)

    # Fixed timetable entries (hard locks)
    fixed_entries: list[FixedTimetableEntry] = (
        db.execute(
            where_tenant(
                select(FixedTimetableEntry)
                .where(FixedTimetableEntry.section_id.in_([s.id for s in sections]))
                .where(FixedTimetableEntry.is_active.is_(True)),
                FixedTimetableEntry,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )

    # Special allotments (hard locked events) applied pre-solve.
    special_allotments: list[SpecialAllotment] = (
        db.execute(
            where_tenant(
                select(SpecialAllotment)
                .where(SpecialAllotment.section_id.in_([s.id for s in sections]))
                .where(SpecialAllotment.is_active.is_(True)),
                SpecialAllotment,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )

    # Curriculum per section
    section_required: dict[str, list[tuple[str, int | None]]] = {}

    # Explicit section → subject mapping (override)
    section_subject_rows = (
        db.execute(
            where_tenant(
                select(SectionSubject.section_id, SectionSubject.subject_id).where(
                    SectionSubject.section_id.in_([s.id for s in sections])
                ),
                SectionSubject,
                tenant_id,
            )
        )
        .all()
    )
    mapped_subjects_by_section = defaultdict(list)
    for sec_id, subj_id in section_subject_rows:
        mapped_subjects_by_section[sec_id].append(subj_id)

    for section in sections:
        mapped = mapped_subjects_by_section.get(section.id, [])
        if mapped:
            # Override: use exactly the mapped subjects (no electives/track inference)
            section_required[section.id] = [(sid, None) for sid in mapped]
            continue

        track_rows = (
            db.execute(
                where_tenant(
                    select(TrackSubject)
                    .where(TrackSubject.program_id == program_id)
                    .where(TrackSubject.academic_year_id == section.academic_year_id)
                    .where(TrackSubject.track == section.track),
                    TrackSubject,
                    tenant_id,
                )
            )
            .scalars()
            .all()
        )
        mandatory = [r for r in track_rows if not r.is_elective]
        elective_options = [r for r in track_rows if r.is_elective]

        # Electives are handled via elective blocks (parallel electives) and are
        # not added as per-section required subjects here.
        section_required[section.id] = [(r.subject_id, r.sessions_override) for r in mandatory]

    # Elective blocks (parallel electives)
    # NOTE: An elective block is scheduled as a shared event for all mapped sections.
    blocks_by_section = defaultdict(list)  # section_id -> [block_id]
    sections_by_block = defaultdict(list)  # block_id -> [section_id]
    elective_block_by_id: dict[str, ElectiveBlock] = {}
    block_subject_pairs_by_block = defaultdict(list)  # block_id -> [(subject_id, teacher_id)]
    elective_block_by_section_subject: dict[tuple[str, str], str] = {}  # (section_id, subject_id) -> block_id

    use_elective_blocks = (
        table_exists(db, "elective_blocks")
        and table_exists(db, "elective_block_subjects")
        and table_exists(db, "section_elective_blocks")
    )

    if use_elective_blocks and sections:
        sec_block_rows = (
            db.execute(
                where_tenant(
                    select(SectionElectiveBlock.section_id, SectionElectiveBlock.block_id)
                    .where(SectionElectiveBlock.section_id.in_([s.id for s in sections])),
                    SectionElectiveBlock,
                    tenant_id,
                )
            )
            .all()
        )
        block_ids = sorted({bid for _sid, bid in sec_block_rows})
        for sid, bid in sec_block_rows:
            blocks_by_section[sid].append(bid)
            sections_by_block[bid].append(sid)

        if block_ids:
            blocks = (
                db.execute(where_tenant(select(ElectiveBlock).where(ElectiveBlock.id.in_(block_ids)), ElectiveBlock, tenant_id))
                .scalars()
                .all()
            )
            elective_block_by_id = {b.id: b for b in blocks}

            bsubs = (
                db.execute(
                    where_tenant(
                        select(ElectiveBlockSubject).where(ElectiveBlockSubject.block_id.in_(block_ids)),
                        ElectiveBlockSubject,
                        tenant_id,
                    )
                )
                .scalars()
                .all()
            )
            for row in bsubs:
                block_subject_pairs_by_block[row.block_id].append((row.subject_id, row.teacher_id))

            # Build a quick lookup from (section, subject) -> elective block.
            # If the same subject appears in multiple blocks for a section, keep the first.
            for sid, bids in blocks_by_section.items():
                for bid in bids:
                    for subj_id, _tid in block_subject_pairs_by_block.get(bid, []):
                        elective_block_by_section_subject.setdefault((sid, subj_id), bid)

    # Allowed slots per section
    allowed_slots_by_section = defaultdict(set)
    for section in sections:
        for w in windows_by_section.get(section.id, []):
            for si in range(w.start_slot_index, w.end_slot_index + 1):
                ts = slot_by_day_index.get((w.day_of_week, si))
                if ts is not None:
                    allowed_slots_by_section[section.id].add(ts.id)

    # Remove run-specific section breaks from the allowed slot pool.
    # Breaks are stored per run (run_id, section_id, slot_id).
    if sections:
        q_breaks = (
            select(SectionBreak.section_id, SectionBreak.slot_id)
            .where(SectionBreak.run_id == run.id)
            .where(SectionBreak.section_id.in_([s.id for s in sections]))
        )
        q_breaks = where_tenant(q_breaks, SectionBreak, tenant_id)
        break_rows = db.execute(q_breaks).all()
        if break_rows:
            for sec_id, slot_id in break_rows:
                allowed_slots_by_section[sec_id].discard(slot_id)

    # Precompute allowed slot indices by (section, day) for faster LAB candidate generation.
    allowed_slot_indices_by_section_day = defaultdict(list)  # (sec_id, day) -> [slot_index]
    for section in sections:
        for slot_id in allowed_slots_by_section.get(section.id, set()):
            day, slot_idx = slot_info.get(slot_id, (None, None))
            if day is None or slot_idx is None:
                continue
            allowed_slot_indices_by_section_day[(section.id, int(day))].append(int(slot_idx))
    for key, arr in allowed_slot_indices_by_section_day.items():
        arr.sort()

    # =========================
    # Apply special allotments pre-solve
    # =========================
    # We treat special allotments as already-scheduled events:
    # - remove occupied slots from variable creation
    # - reduce required session counts accordingly
    # - reserve rooms for greedy room assignment
    # - write these entries directly into timetable_entries

    locked_theory_sessions_by_sec_subj = defaultdict(int)  # (sec_id, subj_id) -> sessions already locked
    locked_theory_sessions_by_sec_subj_day = defaultdict(int)  # (sec_id, subj_id, day) -> locked theory sessions
    locked_lab_sessions_by_sec_subj = defaultdict(int)  # (sec_id, subj_id) -> lab blocks already locked
    locked_lab_sessions_by_sec_subj_day = defaultdict(int)  # (sec_id, subj_id, day) -> locked lab blocks

    locked_section_slots = set()  # (sec_id, slot_id)
    locked_teacher_slots = set()  # (teacher_id, slot_id)
    locked_teacher_slot_day = {}  # (teacher_id, slot_id) -> day

    locked_slot_indices_by_section_day = defaultdict(set)  # (sec_id, day) -> {slot_index}

    special_room_by_section_slot: dict[tuple[str, str], str] = {}
    special_entries_to_write: list[tuple[str, str, str, str, str]] = []  # (sec, subj, teacher, room, slot)

    # Elective-block locks (shared per block):
    # Any lock (special allotment / fixed entry) on a block subject implies the entire block
    # occurrence is fixed to that slot for ALL mapped sections in this solve.
    locked_elective_sessions_by_block = defaultdict(int)  # block_id -> locked occurrences
    locked_elective_sessions_by_block_day = defaultdict(int)  # (block_id, day) -> locked occurrences
    locked_elective_block_slots: set[tuple[str, str]] = set()  # (block_id, slot_id)
    forced_room_by_block_subject_slot: dict[tuple[str, str, str], str] = {}  # (block_id, subject_id, slot_id) -> room_id
    locked_block_theory_room_demand_by_slot = defaultdict(int)  # slot_id -> room demand (normal rooms only)

    for sa in special_allotments:
        subj = subject_by_id.get(sa.subject_id)
        if subj is None:
            continue
        di = slot_info.get(sa.slot_id)
        if di is None:
            continue
        day, slot_idx = int(di[0]), int(di[1])

        if str(subj.subject_type) == "LAB":
            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1
            locked_lab_sessions_by_sec_subj[(sa.section_id, sa.subject_id)] += 1
            locked_lab_sessions_by_sec_subj_day[(sa.section_id, sa.subject_id, day)] += 1

            for j in range(block):
                ts = slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue

                locked_section_slots.add((sa.section_id, ts.id))
                locked_teacher_slots.add((sa.teacher_id, ts.id))
                locked_teacher_slot_day[(sa.teacher_id, ts.id)] = day
                locked_slot_indices_by_section_day[(sa.section_id, day)].add(int(slot_idx + j))

                allowed_slots_by_section[sa.section_id].discard(ts.id)
                special_room_by_section_slot[(sa.section_id, ts.id)] = sa.room_id
                special_entries_to_write.append((sa.section_id, sa.subject_id, sa.teacher_id, sa.room_id, ts.id))
            continue

        # THEORY (and any other non-LAB)
        block_id = elective_block_by_section_subject.get((sa.section_id, sa.subject_id))
        if block_id is not None:
            pairs = block_subject_pairs_by_block.get(block_id, [])
            if pairs:
                # Count and lock this block occurrence once.
                if (block_id, sa.slot_id) not in locked_elective_block_slots:
                    locked_elective_block_slots.add((block_id, sa.slot_id))
                    locked_elective_sessions_by_block[block_id] += 1
                    locked_elective_sessions_by_block_day[(block_id, day)] += 1

                    for sec_id in sections_by_block.get(block_id, []):
                        locked_section_slots.add((sec_id, sa.slot_id))
                        locked_slot_indices_by_section_day[(sec_id, day)].add(int(slot_idx))
                        allowed_slots_by_section[sec_id].discard(sa.slot_id)

                    for _subj_id, teacher_id in pairs:
                        locked_teacher_slots.add((teacher_id, sa.slot_id))
                        locked_teacher_slot_day[(teacher_id, sa.slot_id)] = day

                    # This subject uses a special room and does NOT consume normal room capacity.
                    locked_block_theory_room_demand_by_slot[sa.slot_id] += int(max(0, len(pairs) - 1))

                forced_room_by_block_subject_slot[(block_id, sa.subject_id, sa.slot_id)] = sa.room_id
                # Skip standalone write; this will be emitted with the block.
                continue

        locked_theory_sessions_by_sec_subj[(sa.section_id, sa.subject_id)] += 1
        locked_theory_sessions_by_sec_subj_day[(sa.section_id, sa.subject_id, day)] += 1
        locked_section_slots.add((sa.section_id, sa.slot_id))
        locked_teacher_slots.add((sa.teacher_id, sa.slot_id))
        locked_teacher_slot_day[(sa.teacher_id, sa.slot_id)] = day
        locked_slot_indices_by_section_day[(sa.section_id, day)].add(int(slot_idx))

        allowed_slots_by_section[sa.section_id].discard(sa.slot_id)
        special_room_by_section_slot[(sa.section_id, sa.slot_id)] = sa.room_id
        special_entries_to_write.append((sa.section_id, sa.subject_id, sa.teacher_id, sa.room_id, sa.slot_id))

    def _contiguous_starts(sorted_indices: list[int], block: int):
        if block <= 1:
            for idx in sorted_indices:
                yield idx
            return
        if not sorted_indices:
            return

        run_start = sorted_indices[0]
        prev = sorted_indices[0]
        for idx in sorted_indices[1:]:
            if idx == prev + 1:
                prev = idx
                continue

            run_end = prev
            if (run_end - run_start + 1) >= block:
                for start in range(run_start, run_end - block + 2):
                    yield start

            run_start = idx
            prev = idx

        run_end = prev
        if (run_end - run_start + 1) >= block:
            for start in range(run_start, run_end - block + 2):
                yield start

    # =========================
    # Combined Groups (v2 + legacy fallback)
    # =========================
    # Each combined group schedules sessions together (shared vars).
    # Multiple groups per subject are allowed (v2). For legacy tables, we treat teacher_id as None
    # and fall back to strict per-section teacher assignments.

    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
    if use_v2:
        q_combined = (
            select(CombinedGroup.id, CombinedGroup.subject_id, CombinedGroup.teacher_id, CombinedGroupSection.section_id)
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

    group_sections = defaultdict(list)  # group_id -> [section_id]
    group_subject = {}  # group_id -> subject_id
    group_teacher_id = {}  # group_id -> teacher_id (optional)
    for gid, subj_id, teacher_id, sec_id in combined_rows:
        group_sections[gid].append(sec_id)
        group_subject[gid] = subj_id
        if gid not in group_teacher_id:
            group_teacher_id[gid] = teacher_id

    solve_section_ids = {s.id for s in sections}
    combined_gid_by_sec_subj = {}  # (section_id, subject_id) -> group_id
    for gid in list(group_sections.keys()):
        subj_id = group_subject.get(gid)
        if subj_id is None:
            del group_sections[gid]
            continue
        subj = subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            del group_sections[gid]
            continue

        filtered = [sid for sid in group_sections[gid] if sid in solve_section_ids]
        # Strict rule: must have 2+ sections in this solve.
        if len(set(filtered)) < 2:
            del group_sections[gid]
            continue
        filtered = list(dict.fromkeys(filtered))
        group_sections[gid] = filtered
        for sid in filtered:
            combined_gid_by_sec_subj[(sid, subj_id)] = gid

    # ==========================================
    # Apply fixed entries pre-solve (speed)
    # ==========================================
    # Similar to special allotments, we can treat many fixed entries as already-scheduled:
    # - remove occupied slots from variable creation
    # - reserve rooms for greedy room assignment
    # - write these entries directly into timetable_entries
    #
    # We intentionally skip:
    # - Combined THEORY fixed entries (must drive shared combined vars)
    # Elective-block subject fixed entries are converted into block-level locks.

    fixed_room_by_section_slot: dict[tuple[str, str], str] = {}
    fixed_entries_to_write: list[tuple[str, str, str, str, str]] = []  # (sec, subj, teacher, room, slot)
    locked_fixed_entry_ids: set[str] = set()

    for fe in fixed_entries:
        subj = subject_by_id.get(fe.subject_id)
        if subj is None:
            continue
        di = slot_info.get(fe.slot_id)
        if di is None:
            continue
        day, slot_idx = int(di[0]), int(di[1])

        # Skip combined THEORY here; handled later by forcing combined_x.
        gid = combined_gid_by_sec_subj.get((fe.section_id, fe.subject_id))
        if gid is not None and str(subj.subject_type) == "THEORY":
            continue

        # Elective-block THEORY: lock the entire block occurrence (shared across sections).
        block_id = elective_block_by_section_subject.get((fe.section_id, fe.subject_id))
        if block_id is not None and str(subj.subject_type) == "THEORY":
            pairs = block_subject_pairs_by_block.get(block_id, [])
            if pairs:
                if (block_id, fe.slot_id) not in locked_elective_block_slots:
                    locked_elective_block_slots.add((block_id, fe.slot_id))
                    locked_elective_sessions_by_block[block_id] += 1
                    locked_elective_sessions_by_block_day[(block_id, day)] += 1

                    for sec_id in sections_by_block.get(block_id, []):
                        locked_section_slots.add((sec_id, fe.slot_id))
                        locked_slot_indices_by_section_day[(sec_id, day)].add(int(slot_idx))
                        allowed_slots_by_section[sec_id].discard(fe.slot_id)

                    for _subj_id, teacher_id in pairs:
                        locked_teacher_slots.add((teacher_id, fe.slot_id))
                        locked_teacher_slot_day[(teacher_id, fe.slot_id)] = day

                    # Room capacity: one normal theory room per elective subject.
                    locked_block_theory_room_demand_by_slot[fe.slot_id] += int(len(pairs))

                forced_room_by_block_subject_slot[(block_id, fe.subject_id, fe.slot_id)] = fe.room_id
                locked_fixed_entry_ids.add(str(fe.id))
                continue

        if str(subj.subject_type) == "LAB":
            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1

            locked_lab_sessions_by_sec_subj[(fe.section_id, fe.subject_id)] += 1
            locked_lab_sessions_by_sec_subj_day[(fe.section_id, fe.subject_id, day)] += 1

            for j in range(block):
                ts = slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue

                locked_section_slots.add((fe.section_id, ts.id))
                locked_teacher_slots.add((fe.teacher_id, ts.id))
                locked_teacher_slot_day[(fe.teacher_id, ts.id)] = day
                locked_slot_indices_by_section_day[(fe.section_id, day)].add(int(slot_idx + j))

                allowed_slots_by_section[fe.section_id].discard(ts.id)
                fixed_room_by_section_slot[(fe.section_id, ts.id)] = fe.room_id
                fixed_entries_to_write.append((fe.section_id, fe.subject_id, fe.teacher_id, fe.room_id, ts.id))
            locked_fixed_entry_ids.add(str(fe.id))
            continue

        # THEORY (and any other non-LAB)
        locked_theory_sessions_by_sec_subj[(fe.section_id, fe.subject_id)] += 1
        locked_theory_sessions_by_sec_subj_day[(fe.section_id, fe.subject_id, day)] += 1
        locked_section_slots.add((fe.section_id, fe.slot_id))
        locked_teacher_slots.add((fe.teacher_id, fe.slot_id))
        locked_teacher_slot_day[(fe.teacher_id, fe.slot_id)] = day
        locked_slot_indices_by_section_day[(fe.section_id, day)].add(int(slot_idx))

        allowed_slots_by_section[fe.section_id].discard(fe.slot_id)
        fixed_room_by_section_slot[(fe.section_id, fe.slot_id)] = fe.room_id
        fixed_entries_to_write.append((fe.section_id, fe.subject_id, fe.teacher_id, fe.room_id, fe.slot_id))
        locked_fixed_entry_ids.add(str(fe.id))

    # ==========================================
    # Prune impossible slots for teachers (speed)
    # ==========================================
    # If a teacher is already hard-locked into a slot (special allotment / fixed entry) or
    # the slot is on their weekly off day, then any decision variable that uses
    # that teacher in that slot can never be true.
    teacher_disallowed_slot_ids = defaultdict(set)  # teacher_id -> {slot_id}
    for teacher_id, slot_id in locked_teacher_slots:
        teacher_disallowed_slot_ids[teacher_id].add(slot_id)
    for teacher_id, teacher in teacher_by_id.items():
        if teacher.weekly_off_day is None:
            continue
        off_day = int(teacher.weekly_off_day)
        for ts in slots_by_day.get(off_day, []):
            teacher_disallowed_slot_ids[teacher_id].add(ts.id)

    # Filter LAB candidate indices and other per-day allowed indices after removing locked slots.
    for key, locked_indices in locked_slot_indices_by_section_day.items():
        if not locked_indices:
            continue
        arr = allowed_slot_indices_by_section_day.get(key)
        if not arr:
            continue
        allowed_slot_indices_by_section_day[key] = [i for i in arr if i not in locked_indices]

    model = cp_model.CpModel()

    x = {}  # theory: (sec, subj, slot) -> Bool
    x_by_sec_subj = defaultdict(list)  # (sec, subj) -> [Bool]
    x_by_sec_subj_day = defaultdict(list)  # (sec, subj, day) -> [Bool]

    z = {}  # elective block event: (block, slot) -> Bool
    z_by_block = defaultdict(list)  # block_id -> [Bool]
    z_by_block_day = defaultdict(list)  # (block_id, day) -> [Bool]

    teacher_slot_terms = defaultdict(list)
    section_slot_terms = defaultdict(list)

    # Speed-ups for teacher constraints (load/off day/continuous)
    teacher_all_terms = defaultdict(list)  # teacher_id -> [Bool] (counted per occupied slot)
    teacher_day_terms = defaultdict(list)  # (teacher_id, day) -> [Bool] (counted per occupied slot)
    teacher_active_days = defaultdict(set)  # teacher_id -> set(day)

    # Room-capacity terms (counts concurrent sessions per slot).
    # We model room *capacity* (how many rooms exist) rather than room identity,
    # and do concrete room IDs with a greedy pass after solving.
    room_terms_by_slot = defaultdict(list)  # slot_id -> [Bool] (THEORY + electives + combined theory)
    lab_room_terms_by_slot = defaultdict(list)  # slot_id -> [Bool] (LAB occupancies)

    lab_start = {}  # (sec, subj, day, start_index) -> Bool

    lab_starts_by_sec_subj = defaultdict(list)  # (sec, subj) -> [Bool]
    lab_starts_by_sec_subj_day = defaultdict(list)  # (sec, subj, day) -> [Bool]

    # Combined THEORY vars (shared)
    combined_x = {}  # (group_id, slot_id) -> Bool
    combined_sessions_required = {}  # group_id -> sessions_per_week

    combined_vars_by_gid = defaultdict(list)  # group_id -> [Bool]
    combined_vars_by_gid_day = defaultdict(list)  # (group_id, day) -> [Bool]

    # Add special allotment occupancies as constants (pre-scheduled events)
    for sec_id, slot_id in locked_section_slots:
        section_slot_terms[(sec_id, slot_id)].append(1)
    for teacher_id, slot_id in locked_teacher_slots:
        teacher_slot_terms[(teacher_id, slot_id)].append(1)
        teacher_all_terms[teacher_id].append(1)
        d = locked_teacher_slot_day.get((teacher_id, slot_id))
        if d is not None:
            teacher_day_terms[(teacher_id, int(d))].append(1)
            teacher_active_days[teacher_id].add(int(d))

    # Build variables
    for section in sections:
        for subject_id, sessions_override in section_required.get(section.id, []):
            subj = subject_by_id.get(subject_id)
            if subj is None:
                continue

            assigned_teacher_id = assigned_teacher_by_section_subject.get((section.id, subject_id))
            if assigned_teacher_id is None:
                # Validation should have caught missing assignments; make this pair unschedulable.
                # (This keeps the solver from silently selecting a teacher.)
                continue

            sessions_per_week = sessions_override if sessions_override is not None else subj.sessions_per_week

            # Combined THEORY: handled as a shared variable per group (strict).
            group_id = combined_gid_by_sec_subj.get((section.id, subject_id))
            if group_id is not None and str(subj.subject_type) == "THEORY":
                v = int(sessions_per_week or 0)
                if group_id not in combined_sessions_required:
                    combined_sessions_required[group_id] = v
                continue

            if str(subj.subject_type) == "LAB":
                block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                if block < 1:
                    block = 1
                for day in range(0, 6):
                    indices = allowed_slot_indices_by_section_day.get((section.id, day), [])
                    if len(indices) < block:
                        continue
                    for start_idx in _contiguous_starts(indices, block):
                        covered = []
                        for j in range(block):
                            ts = slot_by_day_index.get((day, start_idx + j))
                            if ts is None:
                                covered = []
                                break
                            covered.append(ts)
                        if not covered:
                            continue

                        # Prune starts that would violate teacher unavailability.
                        if any(ts.id in teacher_disallowed_slot_ids.get(assigned_teacher_id, set()) for ts in covered):
                            continue

                        sv = model.NewBoolVar(f"lab_start_{section.id}_{subject_id}_{day}_{start_idx}")
                        lab_start[(section.id, subject_id, day, start_idx)] = sv
                        lab_starts_by_sec_subj[(section.id, subject_id)].append(sv)
                        lab_starts_by_sec_subj_day[(section.id, subject_id, day)].append(sv)
                        for ts in covered:
                            section_slot_terms[(section.id, ts.id)].append(sv)

                            # Each covered slot consumes a LAB room.
                            lab_room_terms_by_slot[ts.id].append(sv)

                            # Assigned teacher occupies every covered slot when this start is chosen.
                            teacher_slot_terms[(assigned_teacher_id, ts.id)].append(sv)
                            teacher_all_terms[assigned_teacher_id].append(sv)
                            teacher_day_terms[(assigned_teacher_id, day)].append(sv)
                            teacher_active_days[assigned_teacher_id].add(day)

                starts = lab_starts_by_sec_subj.get((section.id, subject_id), [])
                locked = int(locked_lab_sessions_by_sec_subj.get((section.id, subject_id), 0) or 0)
                needed = int(sessions_per_week) - locked
                if needed < 0:
                    model.Add(0 == 1)
                elif starts:
                    model.Add(sum(starts) == int(needed))
                else:
                    model.Add(int(needed) == 0)

                # max_per_day (blocks)
                for day in range(0, 6):
                    day_starts = lab_starts_by_sec_subj_day.get((section.id, subject_id, day), [])
                    locked_day = int(locked_lab_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0)
                    cap = int(subj.max_per_day) - locked_day
                    if cap < 0:
                        model.Add(0 == 1)
                    elif day_starts:
                        model.Add(sum(day_starts) <= int(cap))
                continue

            # THEORY
            for slot_id in sorted(list(allowed_slots_by_section[section.id])):
                # Prune slots that the assigned teacher can never take.
                if slot_id in teacher_disallowed_slot_ids.get(assigned_teacher_id, set()):
                    continue
                xv = model.NewBoolVar(f"x_{section.id}_{subject_id}_{slot_id}")
                x[(section.id, subject_id, slot_id)] = xv
                section_slot_terms[(section.id, slot_id)].append(xv)

                # Consumes one THEORY-capable room in this slot.
                room_terms_by_slot[slot_id].append(xv)

                teacher_slot_terms[(assigned_teacher_id, slot_id)].append(xv)
                teacher_all_terms[assigned_teacher_id].append(xv)
                d = slot_info.get(slot_id, (None, None))[0]
                if d is not None:
                    teacher_day_terms[(assigned_teacher_id, int(d))].append(xv)
                    teacher_active_days[assigned_teacher_id].add(int(d))

                x_by_sec_subj[(section.id, subject_id)].append(xv)
                d = slot_info.get(slot_id, (None, None))[0]
                if d is not None:
                    x_by_sec_subj_day[(section.id, subject_id, int(d))].append(xv)

                tvs = []

                # With strict assignment, teacher is implicit; no extra vars needed.

            terms = x_by_sec_subj.get((section.id, subject_id), [])
            locked = int(locked_theory_sessions_by_sec_subj.get((section.id, subject_id), 0) or 0)
            needed = int(sessions_per_week) - locked
            if needed < 0:
                model.Add(0 == 1)
            elif terms:
                model.Add(sum(terms) == int(needed))
            else:
                model.Add(int(needed) == 0)

            for day in range(0, 6):
                day_x = x_by_sec_subj_day.get((section.id, subject_id, day), [])
                locked_day = int(locked_theory_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0)
                cap = int(subj.max_per_day) - locked_day
                if cap < 0:
                    model.Add(0 == 1)
                elif day_x:
                    model.Add(sum(day_x) <= int(cap))

    effective_teacher_by_gid: dict[uuid.UUID, uuid.UUID] = {}

    # Combined THEORY variables and constraints (shared decision variables)
    for group_id, sec_ids in group_sections.items():
        subj_id = group_subject.get(group_id)
        if subj_id is None:
            continue
        subj = subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            continue

        sessions_per_week = int(combined_sessions_required.get(group_id, int(subj.sessions_per_week) or 0))
        if sessions_per_week <= 0:
            continue

        # Must be allowed for ALL sections in the group.
        allowed = None
        for sid in sec_ids:
            s_allowed = set(allowed_slots_by_section.get(sid, set()))
            allowed = s_allowed if allowed is None else (allowed & s_allowed)
        if not allowed:
            continue

        assigned_teacher_id = group_teacher_id.get(group_id)
        if assigned_teacher_id is None:
            # Legacy fallback: strict combined-class rule (all sections must have same assigned teacher).
            for sid in sec_ids:
                tid = assigned_teacher_by_section_subject.get((sid, subj_id))
                if tid is None:
                    assigned_teacher_id = None
                    break
                if assigned_teacher_id is None:
                    assigned_teacher_id = tid
                elif assigned_teacher_id != tid:
                    assigned_teacher_id = None
                    break
        if assigned_teacher_id is None:
            continue

        effective_teacher_by_gid[group_id] = assigned_teacher_id
        for slot_id in sorted(list(allowed)):
            # Prune slots that the shared teacher can never take.
            if slot_id in teacher_disallowed_slot_ids.get(assigned_teacher_id, set()):
                continue
            gv = model.NewBoolVar(f"cg_{group_id}_{subj_id}_{slot_id}")
            combined_x[(group_id, slot_id)] = gv
            combined_vars_by_gid[group_id].append(gv)
            d = slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                combined_vars_by_gid_day[(group_id, int(d))].append(gv)

            # Section load: each section consumes this slot.
            for sid in sec_ids:
                section_slot_terms[(sid, slot_id)].append(gv)

            # Assigned teacher occupies this slot when the combined session is scheduled.
            teacher_slot_terms[(assigned_teacher_id, slot_id)].append(gv)
            teacher_all_terms[assigned_teacher_id].append(gv)
            d = slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                teacher_day_terms[(assigned_teacher_id, int(d))].append(gv)
                teacher_active_days[assigned_teacher_id].add(int(d))

            # Combined class uses one room total (not per-section).
            room_terms_by_slot[slot_id].append(gv)

        # Total sessions/week for the combined group
        model.Add(sum(combined_vars_by_gid.get(group_id, [])) == int(sessions_per_week))

        # Max per day constraint (applied to the shared schedule)
        for day in range(0, 6):
            day_terms = combined_vars_by_gid_day.get((group_id, day), [])
            if day_terms:
                model.Add(sum(day_terms) <= int(subj.max_per_day))

    # Elective block variables and constraints (shared slot per block)
    for block_id, sec_ids in sections_by_block.items():
        if not sec_ids:
            continue
        pairs = block_subject_pairs_by_block.get(block_id, [])
        if not pairs:
            continue

        # Derive sessions/week and max/day from subjects inside the block.
        subj_objs = [subject_by_id.get(subj_id) for subj_id, _tid in pairs]
        subj_objs = [s for s in subj_objs if s is not None]
        if len(subj_objs) != len(pairs):
            continue
        if any(str(s.subject_type) != "THEORY" for s in subj_objs):
            continue

        sessions_vals = [int(getattr(s, "sessions_per_week", 0) or 0) for s in subj_objs]
        if not sessions_vals or len(set(sessions_vals)) != 1:
            continue
        sessions_per_week = int(sessions_vals[0])
        if sessions_per_week <= 0:
            continue

        max_per_day = min(int(getattr(s, "max_per_day", 1) or 1) for s in subj_objs)
        if max_per_day < 0:
            max_per_day = 0

        # Allowed slots must be in-window for ALL mapped sections.
        allowed = None
        for sid in sec_ids:
            s_allowed = set(allowed_slots_by_section.get(sid, set()))
            allowed = s_allowed if allowed is None else (allowed & s_allowed)
        if not allowed:
            continue

        for slot_id in sorted(list(allowed)):
            # Prune slots where any teacher in the block is unavailable.
            blocked = False
            for _subj_id, teacher_id in pairs:
                if slot_id in teacher_disallowed_slot_ids.get(teacher_id, set()):
                    blocked = True
                    break
            if blocked:
                continue

            zv = model.NewBoolVar(f"z_{block_id}_{slot_id}")
            z[(block_id, slot_id)] = zv
            z_by_block[block_id].append(zv)

            # All mapped sections are occupied when the block occurs.
            for sid in sec_ids:
                section_slot_terms[(sid, slot_id)].append(zv)

            # Room capacity: one THEORY-capable room per elective subject.
            for _subj_id, _teacher_id in pairs:
                room_terms_by_slot[slot_id].append(zv)

            d = slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                z_by_block_day[(block_id, int(d))].append(zv)

            # Every teacher in the block occupies this slot when the block occurs.
            for _subj_id, teacher_id in pairs:
                teacher_slot_terms[(teacher_id, slot_id)].append(zv)
                teacher_all_terms[teacher_id].append(zv)
                if d is not None:
                    teacher_day_terms[(teacher_id, int(d))].append(zv)
                    teacher_active_days[teacher_id].add(int(d))

        terms = z_by_block.get(block_id, [])
        locked = int(locked_elective_sessions_by_block.get(block_id, 0) or 0)
        needed = int(sessions_per_week) - locked
        if needed < 0:
            model.Add(0 == 1)
        elif terms:
            model.Add(sum(terms) == int(needed))
        else:
            model.Add(int(needed) == 0)

        for day in range(0, 6):
            day_terms = z_by_block_day.get((block_id, day), [])
            locked_day = int(locked_elective_sessions_by_block_day.get((block_id, day), 0) or 0)
            cap = int(max_per_day) - locked_day
            if cap < 0:
                model.Add(0 == 1)
            elif day_terms:
                model.Add(sum(day_terms) <= int(cap))

    # =========================
    # Room capacity constraints
    # =========================
    # These prevent schedules that require more simultaneous rooms than exist.
    # Without this, a PROGRAM_GLOBAL run can schedule every section at the same time,
    # and the greedy room assignment will be forced to create room conflicts.
    theory_room_capacity = len(rooms_by_type.get("CLASSROOM", [])) + len(rooms_by_type.get("LT", []))
    lab_room_capacity = len(rooms_by_type.get("LAB", []))

    special_theory_by_slot = defaultdict(int)
    special_lab_by_slot = defaultdict(int)
    for _sec_id, subj_id, _teacher_id, _room_id, slot_id in special_entries_to_write:
        # Special-room locks do not consume normal room capacity.
        room = room_by_id.get(_room_id)
        if room is not None and bool(getattr(room, "is_special", False)):
            continue
        subj = subject_by_id.get(subj_id)
        if subj is not None and str(subj.subject_type) == "LAB":
            special_lab_by_slot[slot_id] += 1
        else:
            special_theory_by_slot[slot_id] += 1

    fixed_theory_by_slot = defaultdict(int)
    fixed_lab_by_slot = defaultdict(int)
    for _sec_id, subj_id, _teacher_id, _room_id, slot_id in fixed_entries_to_write:
        # Fixed entries consume normal room capacity (validation should prevent special rooms here).
        room = room_by_id.get(_room_id)
        if room is not None and bool(getattr(room, "is_special", False)):
            continue
        subj = subject_by_id.get(subj_id)
        if subj is not None and str(subj.subject_type) == "LAB":
            fixed_lab_by_slot[slot_id] += 1
        else:
            fixed_theory_by_slot[slot_id] += 1

    for ts in slots:
        slot_id = ts.id
        model.Add(
            sum(room_terms_by_slot.get(slot_id, []))
            + int(special_theory_by_slot.get(slot_id, 0))
            + int(fixed_theory_by_slot.get(slot_id, 0))
            + int(locked_block_theory_room_demand_by_slot.get(slot_id, 0))
            <= int(theory_room_capacity)
        )
        model.Add(
            sum(lab_room_terms_by_slot.get(slot_id, []))
            + int(special_lab_by_slot.get(slot_id, 0))
            + int(fixed_lab_by_slot.get(slot_id, 0))
            <= int(lab_room_capacity)
        )

    # =========================
    # Apply fixed-entry hard constraints
    # =========================
    def _make_infeasible(_reason: str, *, section_id=None, subject_id=None, teacher_id=None, slot_id=None):
        # Force infeasible via a contradictory constraint.
        # Detailed user-facing conflicts should be raised during validation.
        model.Add(0 == 1)

    for fe in fixed_entries:
        if str(fe.id) in locked_fixed_entry_ids:
            continue
        subj = subject_by_id.get(fe.subject_id)
        if subj is None:
            _make_infeasible(
                "Fixed entry subject is not part of the current solve scope (inactive or out-of-scope).",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue

        di = slot_info.get(fe.slot_id)
        if di is None:
            _make_infeasible(
                "Fixed entry references a time slot that does not exist.",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue
        day, slot_idx = int(di[0]), int(di[1])

        # Combined THEORY: force the shared variable instead of per-section theory vars.
        gid = combined_gid_by_sec_subj.get((fe.section_id, fe.subject_id))
        if gid is not None and str(subj.subject_type) == "THEORY":
            if getattr(fe, "teacher_id", None) is not None:
                expected_tid = group_teacher_id.get(gid)
                if expected_tid is None:
                    # Legacy fallback: strict teacher per section-subject.
                    strict_tid = None
                    for sid in group_sections.get(gid, []):
                        _tid = assigned_teacher_by_section_subject.get((sid, fe.subject_id))
                        if _tid is None:
                            strict_tid = None
                            break
                        if strict_tid is None:
                            strict_tid = _tid
                        elif strict_tid != _tid:
                            strict_tid = None
                            break
                    expected_tid = strict_tid
                if expected_tid is not None and expected_tid != fe.teacher_id:
                    _make_infeasible(
                        "Fixed combined-class teacher does not match the group's assigned teacher.",
                        section_id=fe.section_id,
                        subject_id=fe.subject_id,
                        teacher_id=fe.teacher_id,
                        slot_id=fe.slot_id,
                    )
                    continue

            gv = combined_x.get((gid, fe.slot_id))
            if gv is None:
                _make_infeasible(
                    "Fixed combined-class slot is not allowed for all sections in the group.",
                    section_id=fe.section_id,
                    subject_id=fe.subject_id,
                    teacher_id=fe.teacher_id,
                    slot_id=fe.slot_id,
                )
                continue
            model.Add(gv == 1)

            # Room is applied post-solve per section.
            for sid in group_sections.get(gid, []):
                fixed_room_by_section_slot[(sid, fe.slot_id)] = fe.room_id
            continue

        if str(subj.subject_type) == "LAB":
            sv = lab_start.get((fe.section_id, fe.subject_id, day, slot_idx))
            if sv is None:
                _make_infeasible(
                    "Fixed lab entry must be placed on a valid lab start slot that fits contiguously.",
                    section_id=fe.section_id,
                    subject_id=fe.subject_id,
                    teacher_id=fe.teacher_id,
                    slot_id=fe.slot_id,
                )
                continue
            model.Add(sv == 1)

            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1
            for j in range(block):
                ts = slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue
                fixed_room_by_section_slot[(fe.section_id, ts.id)] = fe.room_id
            continue

        # Regular THEORY
        xv = x.get((fe.section_id, fe.subject_id, fe.slot_id))
        if xv is None:
            _make_infeasible(
                "Fixed entry slot is not allowed for the section (outside window/break) or variable missing.",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue
        model.Add(xv == 1)
        fixed_room_by_section_slot[(fe.section_id, fe.slot_id)] = fe.room_id

    # Section: at most one session per slot
    for section in sections:
        for slot_id in allowed_slots_by_section[section.id]:
            terms = section_slot_terms.get((section.id, slot_id), [])
            if terms:
                model.Add(sum(terms) <= 1)

    # =========================================================
    # Section compactness: max gap between classes per day
    # =========================================================
    # Hard constraint:
    # For a given section and day, between any two scheduled classes
    # there must not be more than 3 consecutive empty slots.
    #
    # Implementation:
    # Let occ[i] be 1 if the section is occupied in the i-th time slot of the day.
    # For any i < j with (j - i - 1) > 3, it is forbidden for occ[i] = occ[j] = 1
    # while all occ[k] = 0 for i < k < j.
    # Linear form:
    #   occ[i] + occ[j] - sum(occ[i+1..j-1]) <= 1
    #
    # Notes:
    # - We rely on the existing per-slot constraint sum(terms) <= 1 to ensure
    #   each occ is a boolean (0/1).
    # - This applies to theory, labs (all covered slots), combined, fixed, special.
    MAX_EMPTY_GAP_SLOTS = 3
    occ_by_section_day: dict[tuple[Any, int], list[tuple[int, cp_model.IntVar]]] = {}
    internal_gap_terms: list[cp_model.IntVar] = []

    for section in sections:
        sec_id = section.id
        for day in range(0, 6):
            day_slots = slots_by_day.get(day, [])
            # Need at least 6 slots in a day to have a gap > 3 between two classes.
            if len(day_slots) < (MAX_EMPTY_GAP_SLOTS + 3):
                continue

            occ_list: list[tuple[int, cp_model.IntVar]] = []
            occ_vars: list[cp_model.IntVar] = []
            for ts in day_slots:
                terms = section_slot_terms.get((sec_id, ts.id), [])
                ov = model.NewBoolVar(f"occ_{sec_id}_{day}_{int(ts.slot_index)}")
                if terms:
                    model.Add(ov == sum(terms))
                else:
                    model.Add(ov == 0)
                occ_list.append((int(ts.slot_index), ov))
                occ_vars.append(ov)

            occ_by_section_day[(sec_id, day)] = occ_list

            # Hard max-gap constraint (O(n^2) per day, but n is small (typically 8)).
            n = len(occ_vars)
            min_dist = MAX_EMPTY_GAP_SLOTS + 2  # distance >= 5 means 4+ empty slots between.
            for i in range(0, n):
                for j in range(i + min_dist, n):
                    middle = occ_vars[i + 1 : j]
                    if middle:
                        model.Add(occ_vars[i] + occ_vars[j] - sum(middle) <= 1)
                    else:
                        model.Add(occ_vars[i] + occ_vars[j] <= 1)

            # Soft compactness penalty: count internal empty slots (empty with at least
            # one occupied slot before and after it in the day).
            prefix: list[cp_model.IntVar] = []
            suffix: list[cp_model.IntVar] = []
            for i in range(0, n):
                pv = model.NewBoolVar(f"sec_has_before_{sec_id}_{day}_{i}")
                model.AddMaxEquality(pv, occ_vars[: i + 1])
                prefix.append(pv)
            for i in range(0, n):
                sv = model.NewBoolVar(f"sec_has_after_{sec_id}_{day}_{i}")
                model.AddMaxEquality(sv, occ_vars[i:])
                suffix.append(sv)

            for i in range(1, n - 1):
                gv = model.NewBoolVar(f"sec_gap_{sec_id}_{day}_{i}")
                # gv == 1 implies: prefix[i-1] == 1, suffix[i+1] == 1, occ[i] == 0
                model.Add(gv <= prefix[i - 1])
                model.Add(gv <= suffix[i + 1])
                model.Add(gv + occ_vars[i] <= 1)
                # If all conditions hold, gv must be 1.
                model.Add(gv >= prefix[i - 1] + suffix[i + 1] - occ_vars[i] - 1)
                internal_gap_terms.append(gv)

    # Teacher no overlap
    for (_teacher_id, _slot_id), terms in teacher_slot_terms.items():
        if terms:
            model.Add(sum(terms) <= 1)

    # Cross-year teacher clash prevention is now handled naturally by the global
    # teacher no-overlap constraint (teacher_slot_terms) because all sections
    # across academic years are scheduled in one model.

    # Teacher weekly leave day (weekly_off_day)
    for teacher_id, teacher in teacher_by_id.items():
        if teacher.weekly_off_day is None:
            continue
        off_day = int(teacher.weekly_off_day)
        if off_day not in teacher_active_days.get(teacher_id, set()):
            continue
        for ts in slots_by_day.get(off_day, []):
            terms = teacher_slot_terms.get((teacher_id, ts.id), [])
            if terms:
                model.Add(sum(terms) == 0)

    # Teacher max_continuous: in any (max_continuous + 1) consecutive slots, schedule <= max_continuous
    for teacher_id, teacher in teacher_by_id.items():
        max_cont = int(teacher.max_continuous)
        if max_cont <= 0:
            continue
        for day in range(0, 6):
            if day not in teacher_active_days.get(teacher_id, set()):
                continue
            day_slots = slots_by_day.get(day, [])
            if len(day_slots) <= max_cont:
                continue
            window_len = max_cont + 1
            for i in range(0, len(day_slots) - window_len + 1):
                window_slots = day_slots[i : i + window_len]
                window_terms = []
                for ts in window_slots:
                    window_terms.extend(teacher_slot_terms.get((teacher_id, ts.id), []))
                if window_terms:
                    model.Add(sum(window_terms) <= max_cont)

    # Teacher load (optional)
    if enforce_teacher_load_limits:
        for teacher_id, teacher in teacher_by_id.items():
            all_terms = teacher_all_terms.get(teacher_id, [])
            if all_terms:
                model.Add(sum(all_terms) <= int(teacher.max_per_week))

            for day in range(0, 6):
                day_terms = teacher_day_terms.get((teacher_id, day), [])
                if day_terms:
                    model.Add(sum(day_terms) <= int(teacher.max_per_day))

    # Objective:
    # - Primary: prefer earlier slots
    # - Secondary: minimize internal gaps per section per day
    PRIMARY_WEIGHT = 1000
    obj_terms = []
    for (_sec, _sid, slot_id), xv in x.items():
        _d, idx = slot_info.get(slot_id, (0, 0))
        obj_terms.append(xv * (idx + 1) * PRIMARY_WEIGHT)
    for z_key, zv in z.items():
        # z keys are (block_id, slot_id) (legacy variants may include section_id too).
        slot_id = None
        if isinstance(z_key, tuple):
            if len(z_key) == 2:
                _bid, slot_id = z_key
            elif len(z_key) == 3:
                _sec, _bid, slot_id = z_key
        if slot_id is None:
            continue
        _d, idx = slot_info.get(slot_id, (0, 0))
        obj_terms.append(zv * (idx + 1) * PRIMARY_WEIGHT)
    for (_sec, _sid, _day, start_idx), sv in lab_start.items():
        obj_terms.append(sv * (start_idx + 1) * PRIMARY_WEIGHT)
    if internal_gap_terms:
        obj_terms.append(sum(internal_gap_terms))
    if obj_terms:
        model.Minimize(sum(obj_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time_seconds)
    solver.parameters.num_search_workers = 8
    if seed is not None:
        solver.parameters.random_seed = int(seed)

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        ortools_status = int(status)
        diagnostics: list[dict] = []
        reason_summary: str | None = None
        if status == cp_model.INFEASIBLE:
            run.status = "INFEASIBLE"
            conflict_type = "INFEASIBLE"
            message = (
                "Solver infeasible due to special locked allotments."
                if special_allotments
                else "Solver could not find a feasible timetable."
            )

            try:
                from solver.solver_diagnostics import run_infeasibility_analysis, summarize_diagnostics

                diagnostics = run_infeasibility_analysis(
                    {
                        "sections": sections,
                        "section_required": section_required,
                        "assigned_teacher_by_section_subject": assigned_teacher_by_section_subject,
                        "subject_by_id": subject_by_id,
                        "teacher_by_id": teacher_by_id,
                        "slots": slots,
                        "slot_info": slot_info,
                        "slot_by_day_index": slot_by_day_index,
                        "windows_by_section": windows_by_section,
                        "fixed_entries": fixed_entries,
                        "special_allotments": special_allotments,
                        "group_sections": group_sections,
                        "group_subject": group_subject,
                        "blocks_by_section": blocks_by_section,
                        "block_subject_pairs_by_block": block_subject_pairs_by_block,
                        "rooms_by_type": rooms_by_type,
                        "room_by_id": room_by_id,
                    }
                )
                reason_summary = summarize_diagnostics(diagnostics)
            except Exception:
                diagnostics = []
                reason_summary = None
        elif status == cp_model.UNKNOWN:
            run.status = "ERROR"
            conflict_type = "TIMEOUT"
            message = "Solver timed out without finding a feasible timetable. Increase max_time_seconds or relax constraints."
        elif hasattr(cp_model, "MODEL_INVALID") and status == cp_model.MODEL_INVALID:
            run.status = "ERROR"
            conflict_type = "MODEL_INVALID"
            message = "Solver model invalid. Check input data and constraints."
        else:
            run.status = "ERROR"
            conflict_type = "SOLVER_ERROR"
            message = "Solver returned an unexpected status."

        conflict = TimetableConflict(
            tenant_id=tenant_id,
            run_id=run.id,
            severity="ERROR",
            conflict_type=conflict_type,
            message=message,
            metadata_json={
                "ortools_status": ortools_status,
                **({"reason_summary": reason_summary} if reason_summary else {}),
                **({"diagnostics": diagnostics} if diagnostics else {}),
            },
        )
        db.add(conflict)
        db.commit()
        return SolveResult(
            status=str(run.status),
            entries_written=0,
            conflicts=[conflict],
            diagnostics=diagnostics,
            reason_summary=reason_summary,
        )

    stmt = delete(TimetableEntry).where(TimetableEntry.run_id == run.id)
    stmt = where_tenant(stmt, TimetableEntry, tenant_id)
    db.execute(stmt)
    entries_written = 0

    objective_score = None
    try:
        objective_score = int(solver.ObjectiveValue())
    except Exception:
        objective_score = None

    # Useful warnings even on FEASIBLE/OPTIMAL runs.
    warnings: list[str] = []
    try:
        # Teacher weekly load usage warnings.
        for teacher_id, teacher in teacher_by_id.items():
            max_week = int(getattr(teacher, "max_per_week", 0) or 0)
            if max_week <= 0:
                continue
            used = 0
            for term in teacher_all_terms.get(teacher_id, []):
                if term == 1:
                    used += 1
                else:
                    used += int(solver.Value(term))
            if used >= int(0.9 * max_week):
                warnings.append(f"Teacher {getattr(teacher, 'code', teacher_id)} assigned {used}/{max_week} weekly load")

        # Room capacity utilization warnings (based on max concurrent occupancies).
        theory_room_capacity = len(rooms_by_type.get("CLASSROOM", [])) + len(rooms_by_type.get("LT", []))
        lab_room_capacity = len(rooms_by_type.get("LAB", []))
        if theory_room_capacity > 0:
            max_used = 0
            for ts in slots:
                slot_id = ts.id
                used = int(special_theory_by_slot.get(slot_id, 0) or 0) + int(fixed_theory_by_slot.get(slot_id, 0) or 0)
                for term in room_terms_by_slot.get(slot_id, []):
                    if term == 1:
                        used += 1
                    else:
                        used += int(solver.Value(term))
                max_used = max(max_used, used)
            if max_used >= int(0.95 * theory_room_capacity):
                warnings.append(f"Room utilization near capacity: max {max_used}/{theory_room_capacity} THEORY rooms used")

        if lab_room_capacity > 0:
            max_used = 0
            for ts in slots:
                slot_id = ts.id
                used = int(special_lab_by_slot.get(slot_id, 0) or 0) + int(fixed_lab_by_slot.get(slot_id, 0) or 0)
                for term in lab_room_terms_by_slot.get(slot_id, []):
                    if term == 1:
                        used += 1
                    else:
                        used += int(solver.Value(term))
                max_used = max(max_used, used)
            if max_used >= int(0.95 * lab_room_capacity):
                warnings.append(f"Room utilization near capacity: max {max_used}/{lab_room_capacity} LAB rooms used")
    except Exception:
        warnings = []

    solver_stats = {
        "ortools_status": int(status),
        "wall_time_seconds": float(getattr(solver, "WallTime", lambda: 0.0)()),
        "num_branches": int(getattr(solver, "NumBranches", lambda: 0)()),
        "num_conflicts": int(getattr(solver, "NumConflicts", lambda: 0)()),
        "status_name": cp_model.CpSolverStatus.Name(int(status))
        if hasattr(cp_model, "CpSolverStatus") and hasattr(cp_model.CpSolverStatus, "Name")
        else str(int(status)),
        "require_optimal": bool(require_optimal),
    }

    # Section gap metrics (based on the same occ variables used for constraints/objective).
    try:
        gap_pairs = 0
        gap_sum = 0
        max_gap = 0
        for (_sec_id, _day), occ_list in occ_by_section_day.items():
            occupied_indices = [idx for idx, ov in occ_list if int(solver.Value(ov)) == 1]
            if len(occupied_indices) < 2:
                continue
            occupied_indices.sort()
            for a, b in zip(occupied_indices, occupied_indices[1:]):
                g = int(b) - int(a) - 1
                if g < 0:
                    g = 0
                gap_sum += g
                gap_pairs += 1
                max_gap = max(max_gap, g)
        solver_stats["section_gap_max_empty_slots"] = int(max_gap)
        solver_stats["section_gap_avg_empty_slots"] = float(gap_sum / gap_pairs) if gap_pairs else 0.0
        if internal_gap_terms:
            solver_stats["section_internal_gap_slots"] = int(sum(int(solver.Value(v)) for v in internal_gap_terms))
    except Exception:
        pass

    # Greedy room assignment after solver (keeps CP-SAT model tractable).
    used_rooms_by_slot = defaultdict(set)  # slot_id -> set(room_id)

    # Fail-fast invariants (avoid relying on DB constraint errors).
    # DB rules (see migrations):
    # - room-slot uniqueness is enforced only for rows with combined_class_id IS NULL
    # - section-slot uniqueness is enforced only for rows with elective_block_id IS NULL
    seen_uncombined_room_slot: set[tuple[str, str]] = set()  # (room_id, slot_id)
    seen_non_elective_section_slot: set[tuple[str, str]] = set()  # (section_id, slot_id)
    seen_teacher_slot_event: dict[tuple[str, str], str | None] = {}  # (teacher_id, slot_id) -> combined_class_id

    def _sid(slot_id) -> str:
        return str(slot_id)

    def _rid(room_id) -> str:
        return str(room_id)

    def _room_conflict_group_id(*, room_id, slot_id) -> uuid.UUID:
        # Used only to bypass the partial unique index on (run_id, room_id, slot_id)
        # for non-combined entries when we must persist room conflicts as warnings.
        return uuid.uuid5(uuid.NAMESPACE_OID, f"ROOM_CONFLICT:{run.id}:{room_id}:{slot_id}")

    def _elective_group_id(*, block_id, subject_id, slot_id) -> uuid.UUID:
        # Elective blocks intentionally create multiple timetable_entries with the same (run, room, slot)
        # (one per mapped section) for the SAME physical event. Mark these rows as "combined" so they
        # bypass the uncombined room-slot unique index.
        return uuid.uuid5(uuid.NAMESPACE_OID, f"ELECTIVE_BLOCK:{run.id}:{block_id}:{subject_id}:{slot_id}")

    def _assert_entry_invariants(entry: TimetableEntry) -> None:
        sec_id = str(entry.section_id)
        teacher_id = str(entry.teacher_id)
        room_id = str(entry.room_id)
        slot_id = str(entry.slot_id)
        combined_id = str(entry.combined_class_id) if entry.combined_class_id is not None else None

        if entry.elective_block_id is None:
            k = (sec_id, slot_id)
            if k in seen_non_elective_section_slot:
                raise SolverInvariantError(
                    "SECTION_SLOT_DUPLICATE",
                    "Generated duplicate non-elective section+slot entry before DB insert.",
                    details={"section_id": sec_id, "slot_id": slot_id, "run_id": str(run.id)},
                )
            seen_non_elective_section_slot.add(k)

        if entry.combined_class_id is None:
            k = (room_id, slot_id)
            if k in seen_uncombined_room_slot:
                raise SolverInvariantError(
                    "ROOM_SLOT_DUPLICATE",
                    "Generated duplicate uncombined room+slot entry before DB insert.",
                    details={"room_id": room_id, "slot_id": slot_id, "run_id": str(run.id)},
                )
            seen_uncombined_room_slot.add(k)

        tk = (teacher_id, slot_id)
        if tk not in seen_teacher_slot_event:
            seen_teacher_slot_event[tk] = combined_id
        else:
            prev = seen_teacher_slot_event[tk]
            if prev != combined_id:
                raise SolverInvariantError(
                    "TEACHER_DOUBLE_BOOKING",
                    "Generated teacher slot conflict before DB insert.",
                    details={
                        "teacher_id": teacher_id,
                        "slot_id": slot_id,
                        "run_id": str(run.id),
                        "combined_class_id_prev": prev,
                        "combined_class_id_new": combined_id,
                    },
                )

    conflicting_special_room_slots: set[tuple[str, str]] = set()  # (section_id, slot_id)
    conflicting_fixed_room_slots: set[tuple[str, str]] = set()  # (section_id, slot_id)

    # Reserve rooms for special allotments (and warn on locked room conflicts).
    for (sec_id, slot_id), room_id in special_room_by_section_slot.items():
        sid = _sid(slot_id)
        rid = _rid(room_id)
        if rid in used_rooms_by_slot[sid]:
            conflicting_special_room_slots.add((str(sec_id), str(slot_id)))
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="SPECIAL_ROOM_CONFLICT",
                    message="Special allotment room is already used in this slot by another locked assignment.",
                    section_id=sec_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={},
                )
            )
        used_rooms_by_slot[sid].add(rid)

    # Reserve rooms for fixed entries (and warn on fixed room conflicts).
    for (sec_id, slot_id), room_id in fixed_room_by_section_slot.items():
        sid = _sid(slot_id)
        rid = _rid(room_id)
        if rid in used_rooms_by_slot[sid]:
            conflicting_fixed_room_slots.add((str(sec_id), str(slot_id)))
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="FIXED_ROOM_CONFLICT",
                    message="Fixed entry room is already used in this slot by another fixed assignment.",
                    section_id=sec_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={},
                )
            )
        used_rooms_by_slot[sid].add(rid)

    # Write special allotments into the run output (they're already fully specified).
    for sec_id, subj_id, teacher_id, room_id, slot_id in special_entries_to_write:
        combined_conflict_id = None
        if (str(sec_id), str(slot_id)) in conflicting_special_room_slots:
            combined_conflict_id = _room_conflict_group_id(room_id=room_id, slot_id=slot_id)
        entry = TimetableEntry(
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )
        _assert_entry_invariants(entry)
        db.add(entry)
        entries_written += 1

    # Write pre-locked fixed entries into the run output.
    for sec_id, subj_id, teacher_id, room_id, slot_id in fixed_entries_to_write:
        combined_conflict_id = None
        if (str(sec_id), str(slot_id)) in conflicting_fixed_room_slots:
            combined_conflict_id = _room_conflict_group_id(room_id=room_id, slot_id=slot_id)
        entry = TimetableEntry(
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )
        _assert_entry_invariants(entry)
        db.add(entry)
        entries_written += 1

    def pick_room(slot_id, subject_type: str) -> tuple[str | None, bool]:
        sid = _sid(slot_id)
        candidates = []
        if subject_type == "LAB":
            candidates = rooms_by_type.get("LAB", [])
        else:
            candidates = [*rooms_by_type.get("CLASSROOM", []), *rooms_by_type.get("LT", [])]

        if not candidates:
            return None, False

        for room in candidates:
            rid = _rid(room.id)
            if rid not in used_rooms_by_slot[sid]:
                used_rooms_by_slot[sid].add(rid)
                return room.id, True

        # None free; return first with conflict
        if getattr(settings, "solver_strict_mode", False):
            raise SolverInvariantError(
                "NO_ROOM_AVAILABLE",
                "No free room available for this slot.",
                details={"slot_id": str(slot_id), "subject_type": str(subject_type), "run_id": str(run.id)},
            )
        used_rooms_by_slot[sid].add(_rid(candidates[0].id))
        return candidates[0].id, False

    def pick_lt_room(slot_id) -> tuple[str | None, bool]:
        sid = _sid(slot_id)
        # Electives/combined classes prefer LT, but can fall back to CLASSROOM
        # to match the room-capacity constraints (LT + CLASSROOM pool).
        candidates = [*rooms_by_type.get("LT", []), *rooms_by_type.get("CLASSROOM", [])]
        if not candidates:
            return None, False
        for room in candidates:
            rid = _rid(room.id)
            if rid not in used_rooms_by_slot[sid]:
                used_rooms_by_slot[sid].add(rid)
                return room.id, True
        used_rooms_by_slot[sid].add(_rid(candidates[0].id))
        if getattr(settings, "solver_strict_mode", False):
            raise SolverInvariantError(
                "NO_ROOM_AVAILABLE",
                "No free LT/CLASSROOM available for this slot.",
                details={"slot_id": str(slot_id), "room_pool": "LT+CLASSROOM", "run_id": str(run.id)},
            )
        return candidates[0].id, False

    def pick_room_for_block(slot_ids: list[str]) -> tuple[str | None, bool]:
        candidates = rooms_by_type.get("LAB", [])
        if not candidates:
            return None, False

        # Prefer a room free in ALL slots of the block.
        for room in candidates:
            rid = _rid(room.id)
            if all(rid not in used_rooms_by_slot[_sid(sid)] for sid in slot_ids):
                for sid in slot_ids:
                    used_rooms_by_slot[_sid(sid)].add(rid)
                return room.id, True

        # None free for the whole block; pick the first and mark conflicts.
        if getattr(settings, "solver_strict_mode", False):
            raise SolverInvariantError(
                "NO_ROOM_AVAILABLE",
                "No single lab room available for the full lab block.",
                details={"slot_ids": list(slot_ids), "room_pool": "LAB", "run_id": str(run.id)},
            )
        room_id = candidates[0].id
        for sid in slot_ids:
            used_rooms_by_slot[_sid(sid)].add(_rid(room_id))
        return room_id, False

    for (sec_id, subj_id, slot_id), xv in x.items():
        if solver.Value(xv) != 1:
            continue
        subj = subject_by_id.get(subj_id)
        teacher_id = assigned_teacher_by_section_subject.get((sec_id, subj_id))
        if teacher_id is None or subj is None:
            continue
        fixed_room = fixed_room_by_section_slot.get((sec_id, slot_id))
        if fixed_room is not None:
            room_id, ok_room = fixed_room, True
        else:
            room_id, ok_room = pick_room(slot_id, str(subj.subject_type))
        if room_id is None:
            continue

        combined_conflict_id = None
        if fixed_room is not None and (str(sec_id), str(slot_id)) in conflicting_fixed_room_slots:
            combined_conflict_id = _room_conflict_group_id(room_id=room_id, slot_id=slot_id)
        elif not ok_room:
            combined_conflict_id = _room_conflict_group_id(room_id=room_id, slot_id=slot_id)

        if not ok_room:
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="NO_ROOM_AVAILABLE",
                    message="No free room available for this slot; assigned a conflicting room.",
                    section_id=sec_id,
                    subject_id=subj_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={"subject_type": str(subj.subject_type)},
                )
            )
        entry = TimetableEntry(
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )
        _assert_entry_invariants(entry)
        db.add(entry)
        entries_written += 1

    # Elective block entries (one per subject-teacher pair; grouped by elective_block_id)
    # Note: A block occurrence is a single shared event across all mapped sections.
    chosen_room_by_block_slot_subject: dict[tuple[Any, Any, Any], tuple[Any, bool]] = {}

    def _emit_block_occurrence(block_id: Any, slot_id: Any):
        nonlocal entries_written
        pairs = block_subject_pairs_by_block.get(block_id, [])
        if not pairs:
            return

        # Pick (or reuse) one room per subject for this block occurrence.
        for subj_id, _teacher_id in pairs:
            if (block_id, slot_id, subj_id) in chosen_room_by_block_slot_subject:
                continue
            forced = forced_room_by_block_subject_slot.get((block_id, subj_id, slot_id))
            if forced is not None:
                sid = _sid(slot_id)
                rid = _rid(forced)
                ok_room = rid not in used_rooms_by_slot[sid]
                used_rooms_by_slot[sid].add(rid)
                if (not ok_room) and getattr(settings, "solver_strict_mode", False):
                    raise SolverInvariantError(
                        "NO_ROOM_AVAILABLE",
                        "Forced elective room is already occupied in this slot.",
                        details={"slot_id": str(slot_id), "room_id": str(forced), "run_id": str(run.id)},
                    )
                chosen_room_by_block_slot_subject[(block_id, slot_id, subj_id)] = (forced, ok_room)
                continue

            room_id, ok_room = pick_lt_room(slot_id)
            if room_id is None:
                continue
            chosen_room_by_block_slot_subject[(block_id, slot_id, subj_id)] = (room_id, ok_room)

        for sec_id in sections_by_block.get(block_id, []):
            for subj_id, teacher_id in pairs:
                picked = chosen_room_by_block_slot_subject.get((block_id, slot_id, subj_id))
                if picked is None:
                    continue
                room_id, ok_room = picked
                combined_conflict_id = (
                    _elective_group_id(block_id=block_id, subject_id=subj_id, slot_id=slot_id)
                    if ok_room
                    else _room_conflict_group_id(room_id=room_id, slot_id=slot_id)
                )

                if not ok_room:
                    db.add(
                        TimetableConflict(
                            tenant_id=tenant_id,
                            run_id=run.id,
                            severity="WARN",
                            conflict_type="NO_LT_ROOM_AVAILABLE",
                            message="No free LT room available for this elective block slot; assigned a conflicting LT.",
                            section_id=sec_id,
                            subject_id=subj_id,
                            teacher_id=teacher_id,
                            room_id=room_id,
                            slot_id=slot_id,
                            metadata_json={"elective_block_id": str(block_id)},
                        )
                    )
                entry = TimetableEntry(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
                    section_id=sec_id,
                    subject_id=subj_id,
                    teacher_id=teacher_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    combined_class_id=combined_conflict_id,
                    elective_block_id=block_id,
                )
                _assert_entry_invariants(entry)
                db.add(entry)
                entries_written += 1

    # Emit locked block occurrences first.
    for block_id, slot_id in sorted(list(locked_elective_block_slots), key=lambda x: (str(x[0]), str(x[1]))):
        _emit_block_occurrence(block_id, slot_id)

    # Emit solver-chosen block occurrences.
    for (block_id, slot_id), zv in z.items():
        if solver.Value(zv) != 1:
            continue
        _emit_block_occurrence(block_id, slot_id)

    # Combined THEORY entries (shared decision variable expanded to per-section rows)
    for (group_id, slot_id), gv in combined_x.items():
        if solver.Value(gv) != 1:
            continue

        subj_id = group_subject.get(group_id)
        if subj_id is None:
            continue

        chosen_t = effective_teacher_by_gid.get(group_id)
        if chosen_t is None:
            # Legacy fallback: strict teacher across sections.
            for sec_id in group_sections.get(group_id, []):
                tid = assigned_teacher_by_section_subject.get((sec_id, subj_id))
                if tid is None:
                    chosen_t = None
                    break
                if chosen_t is None:
                    chosen_t = tid
                elif chosen_t != tid:
                    chosen_t = None
                    break
        if chosen_t is None:
            continue

        # If any section in the group has a fixed room for this slot, prefer it.
        fixed_rooms = [fixed_room_by_section_slot.get((sid, slot_id)) for sid in group_sections.get(group_id, [])]
        fixed_rooms = [r for r in fixed_rooms if r is not None]
        if fixed_rooms:
            room_id, ok_room = fixed_rooms[0], True
        else:
            room_id, ok_room = pick_lt_room(slot_id)
        if room_id is None:
            continue
        if not ok_room:
            db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="NO_LT_ROOM_AVAILABLE",
                    message="No free LT room available for this combined class slot; assigned a conflicting LT.",
                    section_id=group_sections.get(group_id, [None])[0],
                    subject_id=subj_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={"combined_group_id": str(group_id)},
                )
            )

        for sec_id in group_sections.get(group_id, []):
            entry = TimetableEntry(
                tenant_id=tenant_id,
                run_id=run.id,
                academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
                section_id=sec_id,
                subject_id=subj_id,
                teacher_id=chosen_t,
                room_id=fixed_room_by_section_slot.get((sec_id, slot_id)) or room_id,
                slot_id=slot_id,
                combined_class_id=group_id,
            )
            _assert_entry_invariants(entry)
            db.add(entry)
            entries_written += 1

    # Labs
    for (sec_id, subj_id, day, start_idx), sv in lab_start.items():
        if solver.Value(sv) != 1:
            continue
        subj = subject_by_id.get(subj_id)
        if subj is None:
            continue
        block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
        if block < 1:
            block = 1
        chosen_t = assigned_teacher_by_section_subject.get((sec_id, subj_id))
        if chosen_t is None:
            continue

        block_slots: list[TimeSlot] = []
        for j in range(block):
            ts = slot_by_day_index.get((day, start_idx + j))
            if ts is not None:
                block_slots.append(ts)

        slot_ids = [str(ts.id) for ts in block_slots]
        if not slot_ids:
            continue

        fixed_rooms = [fixed_room_by_section_slot.get((sec_id, sid)) for sid in slot_ids]
        fixed_rooms = [r for r in fixed_rooms if r is not None]
        if fixed_rooms:
            room_id, ok_room = fixed_rooms[0], True
        else:
            room_id, ok_room = pick_room_for_block(slot_ids)
        if room_id is None:
            continue

        combined_conflict_id = None if ok_room else _room_conflict_group_id(room_id=room_id, slot_id=str(slot_ids[0]))

        for j in range(block):
            ts = slot_by_day_index.get((day, start_idx + j))
            if ts is None:
                continue
            if not ok_room:
                db.add(
                    TimetableConflict(
                        tenant_id=tenant_id,
                        run_id=run.id,
                        severity="WARN",
                        conflict_type="NO_ROOM_AVAILABLE",
                        message="No single lab room available for the full lab block; assigned a conflicting room.",
                        section_id=sec_id,
                        subject_id=subj_id,
                        room_id=room_id,
                        slot_id=ts.id,
                        metadata_json={"subject_type": "LAB"},
                    )
                )
            entry = TimetableEntry(
                tenant_id=tenant_id,
                run_id=run.id,
                academic_year_id=section_year_by_id.get(sec_id) or run.academic_year_id,
                section_id=sec_id,
                subject_id=subj_id,
                teacher_id=chosen_t,
                room_id=room_id,
                slot_id=ts.id,
                combined_class_id=combined_conflict_id,
            )
            _assert_entry_invariants(entry)
            db.add(entry)
            entries_written += 1

    if status == cp_model.OPTIMAL:
        run.status = "OPTIMAL"
    elif require_optimal:
        # CP-SAT returns FEASIBLE when a solution exists but optimality is not proven
        # (typically due to time limit). Treat this as a distinct status so the UI/API
        # never implies optimality.
        run.status = "SUBOPTIMAL"
        warnings.append("A feasible timetable was found, but optimality was not proven (SUBOPTIMAL).")
        db.add(
            TimetableConflict(
                tenant_id=tenant_id,
                run_id=run.id,
                severity="WARN",
                conflict_type="SUBOPTIMAL",
                message="Feasible timetable found but optimality not proven (time limit reached before proving OPTIMAL).",
                metadata_json={"max_time_seconds": float(max_time_seconds)},
            )
        )
    else:
        run.status = "FEASIBLE"
    run.solver_version = "cp-sat-v1"
    try:
        db.commit()
    except IntegrityError:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    return SolveResult(
        status=str(run.status),
        entries_written=entries_written,
        conflicts=[],
        objective_score=objective_score,
        warnings=warnings,
        solver_stats=solver_stats,
    )
