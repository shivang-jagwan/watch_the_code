"""Load all data from the database into SolverContext.

Extracts lines ~120-370 from the original monolithic _solve_program:
sections, slots, rooms, subjects, teachers, teacher assignments,
fixed entries, special allotments, curriculum, elective blocks,
allowed slots, combined groups.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.tenant import where_tenant
from core.db import table_exists
from models.combined_group import CombinedGroup
from models.combined_group_section import CombinedGroupSection
from models.elective_block import ElectiveBlock
from models.elective_block_subject import ElectiveBlockSubject
from models.room import Room
from models.section import Section
from models.section_elective_block import SectionElectiveBlock
from models.section_subject import SectionSubject
from models.section_time_window import SectionTimeWindow
from models.subject import Subject
from models.subject_allowed_room import SubjectAllowedRoom
from models.teacher import Teacher
from models.teacher_time_window import TeacherTimeWindow
from models.teacher_subject_section import TeacherSubjectSection
from models.time_slot import TimeSlot
from models.track_subject import TrackSubject
from models.fixed_timetable_entry import FixedTimetableEntry
from models.special_allotment import SpecialAllotment

from solver.context import SolverContext


def load_all(ctx: SolverContext) -> None:
    """Populate *ctx* with all data required for the solve."""
    db = ctx.db
    tenant_id = ctx.tenant_id
    program_id = ctx.program_id
    academic_year_id = ctx.academic_year_id

    # --- Sections ------------------------------------------------------------
    q_sections = (
        select(Section)
        .where(Section.program_id == program_id)
        .where(Section.is_active.is_(True))
    )
    q_sections = where_tenant(q_sections, Section, tenant_id)
    if academic_year_id is not None:
        q_sections = q_sections.where(Section.academic_year_id == academic_year_id)

    ctx.sections = db.execute(q_sections.order_by(Section.code)).scalars().all()
    ctx.section_year_by_id = {s.id: s.academic_year_id for s in ctx.sections}
    ctx.solve_year_ids = sorted({s.academic_year_id for s in ctx.sections})

    # --- Time slots ----------------------------------------------------------
    ctx.slots = db.execute(where_tenant(select(TimeSlot), TimeSlot, tenant_id)).scalars().all()
    ctx.slot_by_day_index = {(s.day_of_week, s.slot_index): s for s in ctx.slots}
    ctx.slot_info = {s.id: (s.day_of_week, s.slot_index) for s in ctx.slots}
    ctx.lunch_slot_ids = {s.id for s in ctx.slots if bool(getattr(s, "is_lunch_break", False))}
    for s in ctx.slots:
        ctx.slots_by_day[s.day_of_week].append(s)
    for d in ctx.slots_by_day:
        ctx.slots_by_day[d].sort(key=lambda x: x.slot_index)

    # --- Section time windows ------------------------------------------------
    q_windows = select(SectionTimeWindow).where(
        SectionTimeWindow.section_id.in_([s.id for s in ctx.sections])
    )
    q_windows = where_tenant(q_windows, SectionTimeWindow, tenant_id)
    windows = db.execute(q_windows).scalars().all()
    for w in windows:
        ctx.windows_by_section[w.section_id].append(w)

    # --- Teacher time windows ------------------------------------------------
    # Load availability windows once per solve so _prune_teacher_slots can use
    # them without additional DB calls.
    if ctx.teachers:
        q_twins = select(TeacherTimeWindow).where(
            TeacherTimeWindow.teacher_id.in_([t.id for t in ctx.teachers])
        )
        q_twins = where_tenant(q_twins, TeacherTimeWindow, tenant_id)
        twin_rows = db.execute(q_twins).scalars().all()
        for tw in twin_rows:
            ctx.teacher_windows_by_id[tw.teacher_id].append(tw)

    # --- Rooms ---------------------------------------------------------------
    q_rooms = where_tenant(select(Room).where(Room.is_active.is_(True)), Room, tenant_id)
    ctx.rooms_all = db.execute(q_rooms).scalars().all()
    ctx.room_by_id = {r.id: r for r in ctx.rooms_all}
    for r in ctx.rooms_all:
        if bool(getattr(r, "is_special", False)):
            continue
        ctx.rooms_by_type[str(r.room_type)].append(r)

    # --- Subjects ------------------------------------------------------------
    q_subjects = (
        select(Subject)
        .where(Subject.program_id == program_id)
        .where(Subject.is_active.is_(True))
    )
    if ctx.solve_year_ids:
        q_subjects = q_subjects.where(Subject.academic_year_id.in_(ctx.solve_year_ids))
    q_subjects = where_tenant(q_subjects, Subject, tenant_id)
    ctx.subjects = db.execute(q_subjects).scalars().all()
    ctx.subject_by_id = {s.id: s for s in ctx.subjects}

    # --- Subject → allowed rooms (optional; table may not exist yet) ---------
    _load_subject_allowed_rooms(ctx)

    # --- Teachers ------------------------------------------------------------
    q_teachers = where_tenant(select(Teacher).where(Teacher.is_active.is_(True)), Teacher, tenant_id)
    ctx.teachers = db.execute(q_teachers).scalars().all()
    ctx.teacher_by_id = {t.id: t for t in ctx.teachers}

    # --- Teacher → section-subject assignment --------------------------------
    if ctx.sections:
        rows = db.execute(
            where_tenant(
                select(
                    TeacherSubjectSection.section_id,
                    TeacherSubjectSection.subject_id,
                    TeacherSubjectSection.teacher_id,
                )
                .where(TeacherSubjectSection.section_id.in_([s.id for s in ctx.sections]))
                .where(TeacherSubjectSection.is_active.is_(True)),
                TeacherSubjectSection,
                tenant_id,
            )
        ).all()
        for sec_id, subj_id, teacher_id in rows:
            ctx.assigned_teacher_by_section_subject.setdefault((sec_id, subj_id), teacher_id)

    # --- Fixed timetable entries ---------------------------------------------
    ctx.fixed_entries = (
        db.execute(
            where_tenant(
                select(FixedTimetableEntry)
                .where(FixedTimetableEntry.section_id.in_([s.id for s in ctx.sections]))
                .where(FixedTimetableEntry.is_active.is_(True)),
                FixedTimetableEntry,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )

    # --- Special allotments --------------------------------------------------
    ctx.special_allotments = (
        db.execute(
            where_tenant(
                select(SpecialAllotment)
                .where(SpecialAllotment.section_id.in_([s.id for s in ctx.sections]))
                .where(SpecialAllotment.is_active.is_(True)),
                SpecialAllotment,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )

    # --- Curriculum per section (SectionSubject or TrackSubject) --------------
    section_subject_rows = db.execute(
        where_tenant(
            select(SectionSubject.section_id, SectionSubject.subject_id).where(
                SectionSubject.section_id.in_([s.id for s in ctx.sections])
            ),
            SectionSubject,
            tenant_id,
        )
    ).all()
    mapped_subjects_by_section: dict[Any, list[Any]] = defaultdict(list)
    for sec_id, subj_id in section_subject_rows:
        mapped_subjects_by_section[sec_id].append(subj_id)

    for section in ctx.sections:
        mapped = mapped_subjects_by_section.get(section.id, [])
        if mapped:
            ctx.section_required[section.id] = [(sid, None) for sid in mapped]
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
        ctx.section_required[section.id] = [(r.subject_id, r.sessions_override) for r in mandatory]

    # --- Elective blocks -----------------------------------------------------
    _load_elective_blocks(ctx)

    # --- Allowed slots per section -------------------------------------------
    _load_allowed_slots(ctx)

    # --- Combined groups (v2 + legacy) ---------------------------------------
    _load_combined_groups(ctx)

    # --- Build integer index maps (OPTIMIZATION) -----------------------------
    # Must come after all entities are loaded so the maps are complete.
    _build_index_maps(ctx)

    # --- Build room sort cache (OPTIMIZATION Task 5) -------------------------
    # Must come after rooms and sections are loaded.
    _build_room_cache(ctx)


def _load_subject_allowed_rooms(ctx: SolverContext) -> None:
    """Load subject_allowed_rooms into ctx.allowed_rooms_by_subject.

    If the table does not exist yet (e.g. migration not applied), this is a
    no-op so the solver continues working without the feature.
    """
    db = ctx.db
    tenant_id = ctx.tenant_id

    if not table_exists(db, "subject_allowed_rooms"):
        return
    if not ctx.subjects:
        return

    subject_ids = [s.id for s in ctx.subjects]
    q = (
        select(SubjectAllowedRoom.subject_id, SubjectAllowedRoom.room_id)
        .where(SubjectAllowedRoom.subject_id.in_(subject_ids))
    )
    q = where_tenant(q, SubjectAllowedRoom, tenant_id)
    for subj_id, room_id in db.execute(q).all():
        ctx.allowed_rooms_by_subject.setdefault(subj_id, []).append(room_id)


def _load_elective_blocks(ctx: SolverContext) -> None:
    db = ctx.db
    tenant_id = ctx.tenant_id

    use_elective_blocks = (
        table_exists(db, "elective_blocks")
        and table_exists(db, "elective_block_subjects")
        and table_exists(db, "section_elective_blocks")
    )

    if not use_elective_blocks or not ctx.sections:
        return

    sec_block_rows = db.execute(
        where_tenant(
            select(SectionElectiveBlock.section_id, SectionElectiveBlock.block_id)
            .where(SectionElectiveBlock.section_id.in_([s.id for s in ctx.sections])),
            SectionElectiveBlock,
            tenant_id,
        )
    ).all()
    block_ids = sorted({bid for _sid, bid in sec_block_rows})
    for sid, bid in sec_block_rows:
        ctx.blocks_by_section[sid].append(bid)
        ctx.sections_by_block[bid].append(sid)

    if not block_ids:
        return

    blocks = (
        db.execute(
            where_tenant(
                select(ElectiveBlock).where(ElectiveBlock.id.in_(block_ids)),
                ElectiveBlock,
                tenant_id,
            )
        )
        .scalars()
        .all()
    )
    ctx.elective_block_by_id = {b.id: b for b in blocks}

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
        ctx.block_subject_pairs_by_block[row.block_id].append((row.subject_id, row.teacher_id))

    for sid, bids in ctx.blocks_by_section.items():
        for bid in bids:
            for subj_id, _tid in ctx.block_subject_pairs_by_block.get(bid, []):
                ctx.elective_block_by_section_subject.setdefault((sid, subj_id), bid)


def _load_allowed_slots(ctx: SolverContext) -> None:
    db = ctx.db
    tenant_id = ctx.tenant_id

    for section in ctx.sections:
        for w in ctx.windows_by_section.get(section.id, []):
            for si in range(w.start_slot_index, w.end_slot_index + 1):
                ts = ctx.slot_by_day_index.get((w.day_of_week, si))
                if ts is not None and ts.id not in ctx.lunch_slot_ids:
                    ctx.allowed_slots_by_section[section.id].add(ts.id)

    # Precompute allowed slot indices per (section, day)
    for section in ctx.sections:
        for slot_id in ctx.allowed_slots_by_section.get(section.id, set()):
            day, slot_idx = ctx.slot_info.get(slot_id, (None, None))
            if day is None or slot_idx is None:
                continue
            ctx.allowed_slot_indices_by_section_day[(section.id, int(day))].append(int(slot_idx))
    for key, arr in ctx.allowed_slot_indices_by_section_day.items():
        arr.sort()


def _load_combined_groups(ctx: SolverContext) -> None:
    db = ctx.db
    tenant_id = ctx.tenant_id

    q_combined = (
        select(
            CombinedGroup.id,
            CombinedGroup.subject_id,
            CombinedGroup.teacher_id,
            CombinedGroupSection.section_id,
        )
        .join(CombinedGroupSection, CombinedGroupSection.combined_group_id == CombinedGroup.id)
        .join(Subject, Subject.id == CombinedGroup.subject_id)
        .where(Subject.program_id == ctx.program_id)
        .where(Subject.is_active.is_(True))
    )
    if ctx.solve_year_ids:
        q_combined = q_combined.where(
            CombinedGroup.academic_year_id.in_(ctx.solve_year_ids)
        ).where(Subject.academic_year_id.in_(ctx.solve_year_ids))
    q_combined = where_tenant(q_combined, CombinedGroup, tenant_id)
    q_combined = where_tenant(q_combined, CombinedGroupSection, tenant_id)
    q_combined = where_tenant(q_combined, Subject, tenant_id)
    combined_rows = db.execute(q_combined).all()

    group_sections: dict[Any, list[Any]] = defaultdict(list)
    group_subject: dict[Any, Any] = {}
    group_teacher_id: dict[Any, Any] = {}
    for gid, subj_id, teacher_id, sec_id in combined_rows:
        group_sections[gid].append(sec_id)
        group_subject[gid] = subj_id
        if gid not in group_teacher_id:
            group_teacher_id[gid] = teacher_id

    solve_section_ids = {s.id for s in ctx.sections}
    for gid in list(group_sections.keys()):
        subj_id = group_subject.get(gid)
        if subj_id is None:
            del group_sections[gid]
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            del group_sections[gid]
            continue

        filtered = [sid for sid in group_sections[gid] if sid in solve_section_ids]
        if len(set(filtered)) < 2:
            del group_sections[gid]
            continue
        filtered = list(dict.fromkeys(filtered))
        group_sections[gid] = filtered
        for sid in filtered:
            ctx.combined_gid_by_sec_subj[(sid, subj_id)] = gid

    ctx.group_sections = group_sections
    ctx.group_subject = group_subject
    ctx.group_teacher_id = group_teacher_id


# ── Index maps & pruned slot computation (OPTIMIZATION) ──────────────────────


def _build_room_cache(ctx: SolverContext) -> None:
    """Pre-sort room lists and build per-section best-fit orderings.

    OPTIMIZATION (Task 5): pick_room() previously called
      list(ctx.rooms_by_type.get(...))   — copy each call
      for s in ctx.sections: if s.id == sec_id  — O(S) scan each call
      fits.sort(); too_small.sort()       — O(R log R) each call

    By computing these once here the per-call cost drops to O(1) dict
    lookup + O(R) scan with no copying or sorting.
    """
    cap = lambda r: int(getattr(r, "capacity", 0) or 0)

    ctx.lab_rooms_sorted = sorted(ctx.rooms_by_type.get("LAB", []), key=cap)
    ctx.classroom_rooms_sorted = sorted(ctx.rooms_by_type.get("CLASSROOM", []), key=cap)
    ctx.lt_rooms_sorted = sorted(ctx.rooms_by_type.get("LT", []), key=cap)

    # LT-first: used by pick_lt_room (elective/combined classes)
    ctx.lt_plus_classroom_rooms_sorted = [*ctx.lt_rooms_sorted, *ctx.classroom_rooms_sorted]

    # Theory rooms: CLASSROOM + LT merged and sorted by capacity ASC.
    # This is the base ordering for sections without a known strength.
    ctx.theory_rooms_sorted = sorted(
        [*ctx.rooms_by_type.get("CLASSROOM", []), *ctx.rooms_by_type.get("LT", [])],
        key=cap,
    )

    # Per-section best-fit ordering.
    # For each section we compute two lists:
    #   (section.id, "THEORY")  — best-fit CLASSROOM+LT rooms
    #   (section.id, "LAB")     — best-fit LAB rooms
    # A "best-fit" ordering is: rooms that fit (cap >= strength) sorted cap
    # ASC first, then rooms that are too small sorted cap DESC (best effort).
    # If strength is unknown/0 we use the plain sorted base list.
    for section in ctx.sections:
        strength = int(getattr(section, "strength", 0) or 0)
        for tag, base in [("LAB", ctx.lab_rooms_sorted), ("THEORY", ctx.theory_rooms_sorted)]:
            if strength > 0:
                # base is already sorted cap ASC, so:
                #   fits = rooms with cap >= strength (already in ASC order)
                #   too_small = rooms with cap < strength in DESC order
                #              = reversed slice of the prefix of base
                fits = [r for r in base if cap(r) >= strength]
                too_small = [r for r in reversed(base) if cap(r) < strength]
                ctx.room_candidates_by_section[(section.id, tag)] = fits + too_small
            else:
                ctx.room_candidates_by_section[(section.id, tag)] = base

    # Also build section_by_id for O(1) lookups elsewhere.
    ctx.section_by_id = {s.id: s for s in ctx.sections}


def _build_index_maps(ctx: SolverContext) -> None:
    """Build dense integer index maps for all solver entities.

    OPTIMIZATION: CP-SAT model-building involves millions of dict lookups.
    UUID strings are 36-character objects with expensive hashing.  Mapping
    everything to dense ints (0, 1, 2, …) reduces per-lookup cost and also
    makes tuple keys smaller, which matters for the large x/lab_start dicts.

    The maps are stored in ctx and used exclusively inside variables.py,
    constraints.py, and objective.py.  result_writer.py and room_assigner.py
    keep using UUID keys so no DB-facing code changes.
    """
    for i, s in enumerate(ctx.sections):
        ctx.section_idx[s.id] = i
        ctx.idx_to_section[i] = s.id

    for i, s in enumerate(ctx.subjects):
        ctx.subject_idx[s.id] = i
        ctx.idx_to_subject[i] = s.id

    for i, t in enumerate(ctx.teachers):
        ctx.teacher_idx[t.id] = i
        ctx.idx_to_teacher[i] = t.id

    # Sort slots deterministically: day ASC, slot_index ASC
    sorted_slots = sorted(ctx.slots, key=lambda ts: (ts.day_of_week, ts.slot_index))
    for i, ts in enumerate(sorted_slots):
        ctx.slot_idx_map[ts.id] = i
        ctx.idx_to_slot[i] = ts.id

    for i, r in enumerate(ctx.rooms_all):
        ctx.room_idx[r.id] = i
        ctx.idx_to_room[i] = r.id


def build_pruned_slots(ctx: SolverContext) -> None:
    """Compute pruned slot sets for all variable types — the key domain-pruning step.

    This function is called by cp_sat_solver._solve_program() AFTER
    apply_pre_solve_locks() so that teacher_disallowed_slot_ids is already
    populated.

    Stage 1 — Per-(section, subject) pruning (stored in valid_slots_by_section_subject):
      Filters out slots that violate any of:
        • section time window (captured by allowed_slots_by_section)
        • teacher off-day / locked slots (teacher_disallowed_slot_ids)
        • not already locked by a special-allotment / fixed-entry
        • for LAB subjects: start positions where the full contiguous block
          does NOT fit within the same day's allowed slots

    Stage 2 — Combined-group pruning (stored in valid_slots_for_combined_group):
      For each combined THEORY group, computes the intersection of allowed
      slots across all member sections and removes teacher-blocked slots.
      _create_combined_theory_vars reads these pre-computed lists directly.

    Stage 3 — Elective-batch pruning (stored in valid_slots_for_elective_batch):
      For each (block, batch), intersects the allowed slots of all batch
      sections and removes all elective-teacher-blocked slots.
      _create_elective_block_vars reads these pre-computed lists directly.

    RESULT: CP-SAT variable creation iterates only valid slots for every
    variable type, cutting total variable count by 40–70% on typical datasets.
    """
    dallowed = ctx.teacher_disallowed_slot_ids  # teacher_id → set[slot_id]

    for section in ctx.sections:
        sec_id = section.id
        allowed: set[Any] = ctx.allowed_slots_by_section.get(sec_id, set())
        if not allowed:
            continue

        for subject_id, _sessions_override in ctx.section_required.get(sec_id, []):
            subj = ctx.subject_by_id.get(subject_id)
            if subj is None:
                continue

            teacher_id = ctx.assigned_teacher_by_section_subject.get((sec_id, subject_id))
            if teacher_id is None:
                continue

            teacher_blocked: set[Any] = dallowed.get(teacher_id, set())
            subject_type = str(subj.subject_type)

            if subject_type == "LAB":
                # For LAB, valid positions are contiguous blocks that fit entirely
                # within allowed slots and contain no teacher-blocked slot.
                block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
                if block < 1:
                    block = 1
                pruned: list[Any] = []
                for day in range(6):
                    indices = ctx.allowed_slot_indices_by_section_day.get((sec_id, day), [])
                    if len(indices) < block:
                        continue
                    from solver.pre_solve_locks import contiguous_starts
                    for start_idx in contiguous_starts(indices, block):
                        # Collect all slots in this block; reject if any slot is
                        # teacher-blocked or doesn't exist in the grid.
                        ok = True
                        for j in range(block):
                            ts = ctx.slot_by_day_index.get((day, start_idx + j))
                            if ts is None or ts.id in teacher_blocked:
                                ok = False
                                break
                        if ok:
                            # Store the *start* slot_id (matches lab_start key convention)
                            start_ts = ctx.slot_by_day_index.get((day, start_idx))
                            if start_ts is not None:
                                pruned.append(start_ts.id)
                ctx.valid_slots_by_section_subject[(sec_id, subject_id)] = pruned

            else:
                # THEORY: simply remove teacher-blocked slots from allowed set
                pruned_theory = [
                    slot_id for slot_id in sorted(allowed)
                    if slot_id not in teacher_blocked
                ]
                ctx.valid_slots_by_section_subject[(sec_id, subject_id)] = pruned_theory


    # ── Combined-group pruning ────────────────────────────────────────────
    # Pre-compute the valid slot list for each combined THEORY group so that
    # _create_combined_theory_vars can use a direct lookup instead of
    # recomputing a set intersection at model-build time.
    for group_id, sec_ids in ctx.group_sections.items():
        subj_id = ctx.group_subject.get(group_id)
        if subj_id is None:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            continue

        assigned_teacher_id = ctx.group_teacher_id.get(group_id)
        if assigned_teacher_id is None:
            # Legacy fallback: derive teacher from per-section assignments.
            for sid in sec_ids:
                tid = ctx.assigned_teacher_by_section_subject.get((sid, subj_id))
                if tid is None:
                    assigned_teacher_id = None
                    break
                if assigned_teacher_id is None:
                    assigned_teacher_id = tid
                elif assigned_teacher_id != tid:
                    assigned_teacher_id = None
                    break
        if assigned_teacher_id is None:
            ctx.valid_slots_for_combined_group[group_id] = []
            continue

        combined_allowed: set[Any] | None = None
        for sid in sec_ids:
            s_allowed = set(ctx.allowed_slots_by_section.get(sid, set()))
            combined_allowed = s_allowed if combined_allowed is None else (combined_allowed & s_allowed)
        if not combined_allowed:
            ctx.valid_slots_for_combined_group[group_id] = []
            continue

        teacher_blocked_cg: set[Any] = dallowed.get(assigned_teacher_id, set())
        ctx.valid_slots_for_combined_group[group_id] = sorted(combined_allowed - teacher_blocked_cg)

    # ── Elective-batch pruning ────────────────────────────────────────────
    # apply_pre_solve_locks() already called _ensure_elective_batches(), so
    # ctx.elective_batches_by_block is fully populated here.  Pre-compute
    # per-batch valid slot lists so _create_elective_block_vars does O(1)
    # lookups instead of recomputing intersections at model-build time.
    for block_id, sec_ids_in_block in ctx.sections_by_block.items():
        if not sec_ids_in_block:
            continue
        pairs = ctx.block_subject_pairs_by_block.get(block_id, [])
        if not pairs:
            continue
        eb_subj_objs = [ctx.subject_by_id.get(subj_id) for subj_id, _tid in pairs]
        eb_subj_objs = [s for s in eb_subj_objs if s is not None]
        if len(eb_subj_objs) != len(pairs):
            continue
        if any(str(s.subject_type) != "THEORY" for s in eb_subj_objs):
            continue

        eb_blocked: set[Any] = set()
        for _subj_id, teacher_id in pairs:
            eb_blocked.update(dallowed.get(teacher_id, set()))

        for batch_idx, batch_sec_ids in enumerate(ctx.elective_batches_by_block.get(block_id, [])):
            eb_allowed: set[Any] | None = None
            for sec_id in batch_sec_ids:
                s_allowed = set(ctx.allowed_slots_by_section.get(sec_id, set()))
                eb_allowed = s_allowed if eb_allowed is None else (eb_allowed & s_allowed)
            if not eb_allowed:
                ctx.valid_slots_for_elective_batch[(block_id, batch_idx)] = []
                continue
            ctx.valid_slots_for_elective_batch[(block_id, batch_idx)] = sorted(eb_allowed - eb_blocked)

