"""Create CP-SAT decision variables and per-session constraints.

Extracts lines ~700-1040 from the original _solve_program:
- Locked constant terms (special allotments as 1-constants)
- Per-section per-subject variable creation (theory, LAB, combined, elective block)
- Session count constraints (sum == needed, max_per_day)
- Elective block variable creation + session constraints
- Combined THEORY variable creation + session constraints
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from solver.context import SolverContext
from solver.pre_solve_locks import contiguous_starts, _ensure_elective_batches


def create_variables(ctx: SolverContext) -> None:
    """Create all CP-SAT BoolVars and attach session-count constraints."""
    _add_locked_constant_terms(ctx)
    _create_section_subject_vars(ctx)
    _create_combined_theory_vars(ctx)
    _create_elective_block_vars(ctx)


# ── helpers ──────────────────────────────────────────────────────────────────


def _add_locked_constant_terms(ctx: SolverContext) -> None:
    """Add constant 1-terms for pre-locked special allotments."""
    for sec_id, slot_id in ctx.locked_section_slots:
        ctx.section_slot_terms[(sec_id, slot_id)].append(1)
    for teacher_id, slot_id in ctx.locked_teacher_slots:
        ctx.teacher_slot_terms[(teacher_id, slot_id)].append(1)
        ctx.teacher_all_terms[teacher_id].append(1)
        d = ctx.locked_teacher_slot_day.get((teacher_id, slot_id))
        if d is not None:
            ctx.teacher_day_terms[(teacher_id, int(d))].append(1)
            ctx.teacher_active_days[teacher_id].add(int(d))


def _create_section_subject_vars(ctx: SolverContext) -> None:
    """Create theory x, lab_start, and mark combined slots for each section/subject."""
    model = ctx.model
    for section in ctx.sections:
        for subject_id, sessions_override in ctx.section_required.get(section.id, []):
            subj = ctx.subject_by_id.get(subject_id)
            if subj is None:
                continue

            assigned_teacher_id = ctx.assigned_teacher_by_section_subject.get(
                (section.id, subject_id)
            )
            if assigned_teacher_id is None:
                continue

            sessions_per_week = (
                sessions_override if sessions_override is not None else subj.sessions_per_week
            )

            # Combined THEORY: handled as shared variable per group later.
            group_id = ctx.combined_gid_by_sec_subj.get((section.id, subject_id))
            if group_id is not None and str(subj.subject_type) == "THEORY":
                v = int(sessions_per_week or 0)
                if group_id not in ctx.combined_sessions_required:
                    ctx.combined_sessions_required[group_id] = v
                continue

            if str(subj.subject_type) == "LAB":
                _create_lab_vars(ctx, section, subject_id, subj, assigned_teacher_id, sessions_per_week)
                continue

            # THEORY
            _create_theory_vars(ctx, section, subject_id, subj, assigned_teacher_id, sessions_per_week)


def _create_lab_vars(
    ctx: SolverContext,
    section: Any,
    subject_id: Any,
    subj: Any,
    assigned_teacher_id: Any,
    sessions_per_week: int,
) -> None:
    model = ctx.model
    block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
    if block < 1:
        block = 1
    for day in range(0, 6):
        indices = ctx.allowed_slot_indices_by_section_day.get((section.id, day), [])
        if len(indices) < block:
            continue
        for start_idx in contiguous_starts(indices, block):
            covered = []
            for j in range(block):
                ts = ctx.slot_by_day_index.get((day, start_idx + j))
                if ts is None:
                    covered = []
                    break
                covered.append(ts)
            if not covered:
                continue

            # Prune starts that would violate teacher unavailability.
            if any(
                ts.id in ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set())
                for ts in covered
            ):
                continue

            sv = model.NewBoolVar(f"lab_start_{section.id}_{subject_id}_{day}_{start_idx}")
            ctx.lab_start[(section.id, subject_id, day, start_idx)] = sv
            ctx.lab_starts_by_sec_subj[(section.id, subject_id)].append(sv)
            ctx.lab_starts_by_sec_subj_day[(section.id, subject_id, day)].append(sv)
            for ts in covered:
                ctx.section_slot_terms[(section.id, ts.id)].append(sv)
                ctx.lab_room_terms_by_slot[ts.id].append(sv)
                ctx.teacher_slot_terms[(assigned_teacher_id, ts.id)].append(sv)
                ctx.teacher_all_terms[assigned_teacher_id].append(sv)
                ctx.teacher_day_terms[(assigned_teacher_id, day)].append(sv)
                ctx.teacher_active_days[assigned_teacher_id].add(day)

    starts = ctx.lab_starts_by_sec_subj.get((section.id, subject_id), [])
    locked = int(ctx.locked_lab_sessions_by_sec_subj.get((section.id, subject_id), 0) or 0)
    needed = int(sessions_per_week) - locked
    if needed < 0:
        model.Add(0 == 1)
    elif starts:
        model.Add(sum(starts) == int(needed))
    else:
        model.Add(int(needed) == 0)

    # max_per_day (blocks)
    for day in range(0, 6):
        day_starts = ctx.lab_starts_by_sec_subj_day.get((section.id, subject_id, day), [])
        locked_day = int(
            ctx.locked_lab_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0
        )
        cap = int(subj.max_per_day) - locked_day
        if cap < 0:
            model.Add(0 == 1)
        elif day_starts:
            model.Add(sum(day_starts) <= int(cap))


def _create_theory_vars(
    ctx: SolverContext,
    section: Any,
    subject_id: Any,
    subj: Any,
    assigned_teacher_id: Any,
    sessions_per_week: int,
) -> None:
    model = ctx.model
    for slot_id in sorted(list(ctx.allowed_slots_by_section[section.id])):
        # Prune slots that the assigned teacher can never take.
        if slot_id in ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set()):
            continue
        xv = model.NewBoolVar(f"x_{section.id}_{subject_id}_{slot_id}")
        ctx.x[(section.id, subject_id, slot_id)] = xv
        ctx.section_slot_terms[(section.id, slot_id)].append(xv)

        # Consumes one THEORY-capable room in this slot.
        ctx.room_terms_by_slot[slot_id].append(xv)

        ctx.teacher_slot_terms[(assigned_teacher_id, slot_id)].append(xv)
        ctx.teacher_all_terms[assigned_teacher_id].append(xv)
        d = ctx.slot_info.get(slot_id, (None, None))[0]
        if d is not None:
            ctx.teacher_day_terms[(assigned_teacher_id, int(d))].append(xv)
            ctx.teacher_active_days[assigned_teacher_id].add(int(d))

        ctx.x_by_sec_subj[(section.id, subject_id)].append(xv)
        d = ctx.slot_info.get(slot_id, (None, None))[0]
        if d is not None:
            ctx.x_by_sec_subj_day[(section.id, subject_id, int(d))].append(xv)

    terms = ctx.x_by_sec_subj.get((section.id, subject_id), [])
    locked = int(ctx.locked_theory_sessions_by_sec_subj.get((section.id, subject_id), 0) or 0)
    needed = int(sessions_per_week) - locked
    if needed < 0:
        model.Add(0 == 1)
    elif terms:
        model.Add(sum(terms) == int(needed))
    else:
        model.Add(int(needed) == 0)

    for day in range(0, 6):
        day_x = ctx.x_by_sec_subj_day.get((section.id, subject_id, day), [])
        locked_day = int(
            ctx.locked_theory_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0
        )
        cap = int(subj.max_per_day) - locked_day
        if cap < 0:
            model.Add(0 == 1)
        elif day_x:
            model.Add(sum(day_x) <= int(cap))


def _create_combined_theory_vars(ctx: SolverContext) -> None:
    """Create shared BoolVars for combined THEORY groups."""
    model = ctx.model
    for group_id, sec_ids in ctx.group_sections.items():
        subj_id = ctx.group_subject.get(group_id)
        if subj_id is None:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            continue

        sessions_per_week = int(
            ctx.combined_sessions_required.get(group_id, int(subj.sessions_per_week) or 0)
        )
        if sessions_per_week <= 0:
            continue

        # Must be allowed for ALL sections in the group.
        allowed = None
        for sid in sec_ids:
            s_allowed = set(ctx.allowed_slots_by_section.get(sid, set()))
            allowed = s_allowed if allowed is None else (allowed & s_allowed)
        if not allowed:
            continue

        assigned_teacher_id = ctx.group_teacher_id.get(group_id)
        if assigned_teacher_id is None:
            # Legacy fallback
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
            continue

        ctx.effective_teacher_by_gid[group_id] = assigned_teacher_id
        for slot_id in sorted(list(allowed)):
            if slot_id in ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set()):
                continue
            gv = model.NewBoolVar(f"cg_{group_id}_{subj_id}_{slot_id}")
            ctx.combined_x[(group_id, slot_id)] = gv
            ctx.combined_vars_by_gid[group_id].append(gv)
            d = ctx.slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                ctx.combined_vars_by_gid_day[(group_id, int(d))].append(gv)

            for sid in sec_ids:
                ctx.section_slot_terms[(sid, slot_id)].append(gv)

            ctx.teacher_slot_terms[(assigned_teacher_id, slot_id)].append(gv)
            ctx.teacher_all_terms[assigned_teacher_id].append(gv)
            d = ctx.slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                ctx.teacher_day_terms[(assigned_teacher_id, int(d))].append(gv)
                ctx.teacher_active_days[assigned_teacher_id].add(int(d))

            ctx.room_terms_by_slot[slot_id].append(gv)

        model.Add(sum(ctx.combined_vars_by_gid.get(group_id, [])) == int(sessions_per_week))

        for day in range(0, 6):
            day_terms = ctx.combined_vars_by_gid_day.get((group_id, day), [])
            if day_terms:
                model.Add(sum(day_terms) <= int(subj.max_per_day))


def _create_elective_block_vars(ctx: SolverContext) -> None:
    """Create batch-specific z BoolVars for elective blocks."""
    model = ctx.model
    _ensure_elective_batches(ctx)
    for block_id, sec_ids in ctx.sections_by_block.items():
        if not sec_ids:
            continue
        pairs = ctx.block_subject_pairs_by_block.get(block_id, [])
        if not pairs:
            continue

        subj_objs = [ctx.subject_by_id.get(subj_id) for subj_id, _tid in pairs]
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

        batches = ctx.elective_batches_by_block.get(block_id, [])
        for batch_idx, batch_sec_ids in enumerate(batches):
            allowed = None
            for sec_id in batch_sec_ids:
                s_allowed = set(ctx.allowed_slots_by_section.get(sec_id, set()))
                allowed = s_allowed if allowed is None else (allowed & s_allowed)
            if not allowed:
                continue

            for slot_id in sorted(list(allowed)):
                blocked = False
                for _subj_id, teacher_id in pairs:
                    if slot_id in ctx.teacher_disallowed_slot_ids.get(teacher_id, set()):
                        blocked = True
                        break
                if blocked:
                    continue

                zv = model.NewBoolVar(f"z_{block_id}_{batch_idx}_{slot_id}")
                ctx.z[(block_id, int(batch_idx), slot_id)] = zv
                ctx.z_by_block_batch[(block_id, int(batch_idx))].append(zv)

                for sec_id in batch_sec_ids:
                    ctx.section_slot_terms[(sec_id, slot_id)].append(zv)

                for _subj_id, _teacher_id in pairs:
                    ctx.room_terms_by_slot[slot_id].append(zv)

                d = ctx.slot_info.get(slot_id, (None, None))[0]
                if d is not None:
                    ctx.z_by_block_batch_day[(block_id, int(batch_idx), int(d))].append(zv)

                for _subj_id, teacher_id in pairs:
                    ctx.teacher_slot_terms[(teacher_id, slot_id)].append(zv)
                    ctx.teacher_all_terms[teacher_id].append(zv)
                    if d is not None:
                        ctx.teacher_day_terms[(teacher_id, int(d))].append(zv)
                        ctx.teacher_active_days[teacher_id].add(int(d))

            terms = ctx.z_by_block_batch.get((block_id, int(batch_idx)), [])
            locked = int(ctx.locked_elective_sessions_by_block_batch.get((block_id, int(batch_idx)), 0) or 0)
            needed = int(sessions_per_week) - locked
            if needed < 0:
                model.Add(0 == 1)
            elif terms:
                model.Add(sum(terms) == int(needed))
            else:
                model.Add(int(needed) == 0)

            for day in range(0, 6):
                day_terms = ctx.z_by_block_batch_day.get((block_id, int(batch_idx), day), [])
                locked_day = int(
                    ctx.locked_elective_sessions_by_block_batch_day.get((block_id, int(batch_idx), day), 0) or 0
                )
                cap = int(max_per_day) - locked_day
                if cap < 0:
                    model.Add(0 == 1)
                elif day_terms:
                    model.Add(sum(day_terms) <= int(cap))
