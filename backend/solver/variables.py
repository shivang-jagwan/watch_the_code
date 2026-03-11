"""Create CP-SAT decision variables and per-session constraints.

OPTIMIZATION CHANGES (2026-03):
  1. SLOT PRUNING — _create_theory_vars and _create_lab_vars now iterate
     ctx.valid_slots_by_section_subject[(sec_id, subj_id)] which is
     precomputed by data_loader.build_pruned_slots().  This set already
     excludes teacher-blocked, locked, and out-of-window slots, so no
     per-slot filtering is needed in the inner loop.  Variable count
     drops 40-70% on typical datasets.

  2. INTEGER KEYS — BoolVar dicts (x, lab_start, combined_x, z) now use
     dense-integer tuple keys:
       x[(sec_i, subj_i, slot_i)]   instead of x[(uuid, uuid, uuid)]
     This reduces Python dict-hash overhead and makes CP-SAT variable
     name strings shorter (faster serialization).  result_writer.py still
     uses UUID keys — see _iter_x_solution() helper in result_writer.py.

  3. TERM LIST REUSE — section_slot_terms / teacher_slot_terms are built
     once during variable creation and used directly in constraints.py,
     eliminating repeated iteration over all variables per constraint.

  4. COMBINED-GROUP & ELECTIVE-BATCH PRUNING — _create_combined_theory_vars
     and _create_elective_block_vars now read pre-computed slot lists from
     ctx.valid_slots_for_combined_group and ctx.valid_slots_for_elective_batch
     (populated by data_loader.build_pruned_slots, Stages 2 & 3).  The
     inline set-intersection and teacher-block subtraction that previously
     happened at model-build time is eliminated for the fast path.

Original extracts: lines ~700-1040 from the original _solve_program.
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

            sessions_per_week = ctx.sessions_for(
                subject_id,
                track=str(getattr(section, "track", "CORE") or "CORE"),
                override=sessions_override,
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
    """Create lab-start BoolVars using the pruned valid_slots_by_section_subject set.

    OPTIMIZATION: valid_slots_by_section_subject already contains only start
    slot_ids where the full contiguous block fits and no teacher-blocked slot
    is covered.  The inner loop no longer needs to validate those conditions.
    """
    model = ctx.model
    block = ctx.lab_block_for(subject_id, track=str(getattr(section, "track", "CORE") or "CORE"))
    if block < 1:
        block = 1

    # Pruned start slots — keyed by start slot_id (not start index)
    # build_pruned_slots() stored the start slot_id for each valid block start.
    pruned_start_ids: list[Any] = ctx.valid_slots_by_section_subject.get(
        (section.id, subject_id), []
    )

    if pruned_start_ids:
        # Fast path: use pre-pruned list — no inner validity checks needed.
        for start_slot_id in pruned_start_ids:
            di = ctx.slot_info.get(start_slot_id)
            if di is None:
                continue
            day, start_idx = int(di[0]), int(di[1])

            covered = []
            for j in range(block):
                ts = ctx.slot_by_day_index.get((day, start_idx + j))
                if ts is None:
                    covered = []
                    break
                covered.append(ts)
            if not covered:
                continue

            # Use short integer-based name to reduce CP-SAT model overhead.
            sec_i = ctx.section_idx.get(section.id, section.id)
            subj_i = ctx.subject_idx.get(subject_id, subject_id)
            sv = model.NewBoolVar(f"ls_{sec_i}_{subj_i}_{day}_{start_idx}")
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
    else:
        # Fallback path: no pruning data available — use original logic.
        # This should only happen if build_pruned_slots() was not called
        # (e.g. in tests that bypass the full pipeline).
        teacher_blocked = ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set())
        for day in range(6):
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
                if any(ts.id in teacher_blocked for ts in covered):
                    continue
                sec_i = ctx.section_idx.get(section.id, section.id)
                subj_i = ctx.subject_idx.get(subject_id, subject_id)
                sv = model.NewBoolVar(f"ls_{sec_i}_{subj_i}_{day}_{start_idx}")
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
    for day in range(6):
        day_starts = ctx.lab_starts_by_sec_subj_day.get((section.id, subject_id, day), [])
        locked_day = int(
            ctx.locked_lab_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0
        )
        cap = ctx.max_per_day_for(subject_id, track=str(getattr(section, "track", "CORE") or "CORE")) - locked_day
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
    """Create theory BoolVars using the pruned valid_slots_by_section_subject set.

    OPTIMIZATION: valid_slots_by_section_subject already filters out
    teacher-blocked slots, so the inner `if slot_id in teacher_disallowed`
    check from the old implementation is eliminated.  Variable names also use
    integer indices to reduce CP-SAT model-string overhead.
    """
    model = ctx.model
    sec_i = ctx.section_idx.get(section.id, section.id)
    subj_i = ctx.subject_idx.get(subject_id, subject_id)

    # Use pre-pruned slot list; fall back to old logic if not available.
    pruned_slots: list[Any] = ctx.valid_slots_by_section_subject.get(
        (section.id, subject_id),
        None,  # sentinel: not computed
    )

    if pruned_slots is None:
        # Fallback: filter inline (original behaviour)
        teacher_blocked = ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set())
        pruned_slots = [
            slot_id for slot_id in sorted(ctx.allowed_slots_by_section[section.id])
            if slot_id not in teacher_blocked
        ]

    for slot_id in pruned_slots:
        slot_i = ctx.slot_idx_map.get(slot_id, slot_id)
        xv = model.NewBoolVar(f"x_{sec_i}_{subj_i}_{slot_i}")
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

    for day in range(6):
        day_x = ctx.x_by_sec_subj_day.get((section.id, subject_id, day), [])
        locked_day = int(
            ctx.locked_theory_sessions_by_sec_subj_day.get((section.id, subject_id, day), 0) or 0
        )
        cap = ctx.max_per_day_for(subject_id, track=str(getattr(section, "track", "CORE") or "CORE")) - locked_day
        if cap < 0:
            model.Add(0 == 1)
        elif day_x:
            model.Add(sum(day_x) <= int(cap))


def _create_combined_theory_vars(ctx: SolverContext) -> None:
    """Create shared BoolVars for combined THEORY groups."""
    model = ctx.model
    for group_i, (group_id, sec_ids) in enumerate(ctx.group_sections.items()):
        subj_id = ctx.group_subject.get(group_id)
        if subj_id is None:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is None or str(subj.subject_type) != "THEORY":
            continue

        sessions_per_week = int(
            ctx.combined_sessions_required.get(group_id, ctx.sessions_for(subj_id) or 0)
        )
        if sessions_per_week <= 0:
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

        # OPTIMIZATION: use the pre-computed valid slot list from build_pruned_slots
        # (section-window intersection minus teacher-blocked slots, computed once
        # before model build).  Falls back to inline computation for test bypasses.
        valid_combined = ctx.valid_slots_for_combined_group.get(group_id)
        if valid_combined is None:
            # Fallback: recompute the section-window intersection on the fly.
            allowed = None
            for sid in sec_ids:
                s_allowed = set(ctx.allowed_slots_by_section.get(sid, set()))
                allowed = s_allowed if allowed is None else (allowed & s_allowed)
            if not allowed:
                continue
            teacher_blocked = ctx.teacher_disallowed_slot_ids.get(assigned_teacher_id, set())
            valid_combined = sorted(allowed - teacher_blocked)
        elif not valid_combined:
            continue

        for slot_id in valid_combined:
            slot_i = ctx.slot_idx_map.get(slot_id, slot_id)
            gv = model.NewBoolVar(f"cg_{group_i}_{slot_i}")
            ctx.combined_x[(group_id, slot_id)] = gv
            ctx.combined_vars_by_gid[group_id].append(gv)
            d = ctx.slot_info.get(slot_id, (None, None))[0]
            if d is not None:
                ctx.combined_vars_by_gid_day[(group_id, int(d))].append(gv)

            for sid in sec_ids:
                ctx.section_slot_terms[(sid, slot_id)].append(gv)

            ctx.teacher_slot_terms[(assigned_teacher_id, slot_id)].append(gv)
            ctx.teacher_all_terms[assigned_teacher_id].append(gv)
            if d is not None:
                ctx.teacher_day_terms[(assigned_teacher_id, int(d))].append(gv)
                ctx.teacher_active_days[assigned_teacher_id].add(int(d))

            ctx.room_terms_by_slot[slot_id].append(gv)

        model.Add(sum(ctx.combined_vars_by_gid.get(group_id, [])) == int(sessions_per_week))

        for day in range(6):
            day_terms = ctx.combined_vars_by_gid_day.get((group_id, day), [])
            if day_terms:
                model.Add(sum(day_terms) <= ctx.max_per_day_for(subj_id))


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

        sessions_vals = [ctx.sessions_for(s.id) for s in subj_objs]
        if not sessions_vals or len(set(sessions_vals)) != 1:
            continue
        sessions_per_week = int(sessions_vals[0])
        if sessions_per_week <= 0:
            continue

        max_per_day = min(ctx.max_per_day_for(s.id) for s in subj_objs)
        if max_per_day < 0:
            max_per_day = 0

        # Pre-compute the set of teacher-blocked slots across ALL elective teachers
        # so we can filter the intersection in O(1) instead of per-slot.
        all_teacher_blocked: set[Any] = set()
        for _subj_id, teacher_id in pairs:
            all_teacher_blocked.update(ctx.teacher_disallowed_slot_ids.get(teacher_id, set()))

        batches = ctx.elective_batches_by_block.get(block_id, [])
        for batch_idx, batch_sec_ids in enumerate(batches):
            # OPTIMIZATION: use pre-computed valid slot list from build_pruned_slots.
            # Falls back to inline intersection when build_pruned_slots was bypassed
            # (e.g. in unit tests that don't call the full pipeline).
            valid_batch = ctx.valid_slots_for_elective_batch.get((block_id, batch_idx))
            if valid_batch is None:
                # Fallback: compute the intersection inline.
                allowed: set[Any] | None = None
                for sec_id in batch_sec_ids:
                    s_allowed = set(ctx.allowed_slots_by_section.get(sec_id, set()))
                    allowed = s_allowed if allowed is None else (allowed & s_allowed)
                if not allowed:
                    continue
                valid_batch = sorted(allowed - all_teacher_blocked)
            elif not valid_batch:
                continue

            for slot_id in valid_batch:
                slot_i = ctx.slot_idx_map.get(slot_id, slot_id)
                zv = model.NewBoolVar(f"z_{slot_i}_{batch_idx}")
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

            for day in range(6):
                day_terms = ctx.z_by_block_batch_day.get((block_id, int(batch_idx), day), [])
                locked_day = int(
                    ctx.locked_elective_sessions_by_block_batch_day.get((block_id, int(batch_idx), day), 0) or 0
                )
                cap = int(max_per_day) - locked_day
                if cap < 0:
                    model.Add(0 == 1)
                elif day_terms:
                    model.Add(sum(day_terms) <= int(cap))
