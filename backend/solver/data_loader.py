"""Load all data from the database into SolverContext.

Extracts lines ~120-370 from the original monolithic _solve_program:
sections, slots, rooms, subjects, teachers, teacher assignments,
fixed entries, special allotments, curriculum, elective blocks,
allowed slots, combined groups.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import literal, select
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
from models.section import Section
from models.section_break import SectionBreak
from models.section_elective_block import SectionElectiveBlock
from models.section_subject import SectionSubject
from models.section_time_window import SectionTimeWindow
from models.subject import Subject
from models.teacher import Teacher
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
                if ts is not None:
                    ctx.allowed_slots_by_section[section.id].add(ts.id)

    # Remove section breaks
    if ctx.sections:
        q_breaks = (
            select(SectionBreak.section_id, SectionBreak.slot_id)
            .where(SectionBreak.run_id == ctx.run.id)
            .where(SectionBreak.section_id.in_([s.id for s in ctx.sections]))
        )
        q_breaks = where_tenant(q_breaks, SectionBreak, tenant_id)
        break_rows = db.execute(q_breaks).all()
        if break_rows:
            for sec_id, slot_id in break_rows:
                ctx.allowed_slots_by_section[sec_id].discard(slot_id)

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

    use_v2 = table_exists(db, "combined_groups") and table_exists(db, "combined_group_sections")
    if use_v2:
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
            .where(Subject.program_id == ctx.program_id)
            .where(Subject.is_active.is_(True))
        )
        if ctx.solve_year_ids:
            q_combined = q_combined.where(
                CombinedSubjectGroup.academic_year_id.in_(ctx.solve_year_ids)
            ).where(Subject.academic_year_id.in_(ctx.solve_year_ids))
        q_combined = where_tenant(q_combined, CombinedSubjectGroup, tenant_id)
        q_combined = where_tenant(q_combined, CombinedSubjectSection, tenant_id)
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
