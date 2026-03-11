"""Write solver results into TimetableEntry rows.

Extracts lines ~1775-2091 from the original _solve_program:
- Write special allotment entries
- Write pre-locked fixed entries
- Write solver-chosen theory entries (x vars)
- Write elective block entries (z vars)
- Write combined THEORY entries (combined_x vars)
- Write lab entries (lab_start vars)
- Final status + commit
"""

from __future__ import annotations

import uuid
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from api.tenant import where_tenant
from core.config import settings
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from solver.context import SolverContext, SolverInvariantError, SolveResult
from solver.room_assigner import (
    assert_entry_invariants,
    elective_group_id,
    pick_lt_room,
    pick_room,
    pick_room_for_block,
    reserve_locked_rooms,
    room_conflict_group_id,
)


def write_results(ctx: SolverContext, solver: cp_model.CpSolver, status: int) -> SolveResult:
    """Delete old entries, write all new entries, commit, return SolveResult."""
    db = ctx.db
    run = ctx.run
    tenant_id = ctx.tenant_id

    # Delete previous entries for this run
    stmt = delete(TimetableEntry).where(TimetableEntry.run_id == run.id)
    stmt = where_tenant(stmt, TimetableEntry, tenant_id)
    db.execute(stmt)

    # Objective score
    try:
        ctx.objective_score = int(solver.ObjectiveValue())
    except Exception:
        ctx.objective_score = None

    # Optimality bound & gap
    try:
        ctx.best_objective_bound = int(solver.BestObjectiveBound())
        if ctx.objective_score is not None:
            ctx.optimality_gap = max(0, ctx.objective_score - ctx.best_objective_bound)
    except Exception:
        ctx.best_objective_bound = None
        ctx.optimality_gap = None

    # Solve wall-time
    try:
        ctx.solve_time_seconds = float(solver.WallTime())
    except Exception:
        ctx.solve_time_seconds = None

    # Warnings
    _compute_warnings(ctx, solver)

    # Solver stats
    _compute_solver_stats(ctx, solver, status)

    # Reserve rooms for locked entries
    reserve_locked_rooms(ctx)

    # Write all entry types
    _write_special_entries(ctx)
    _write_fixed_entries(ctx)
    _write_theory_entries(ctx, solver)
    _write_elective_block_entries(ctx, solver)
    _write_combined_theory_entries(ctx, solver)
    _write_lab_entries(ctx, solver)

    # Final status and human-readable message
    if status == cp_model.OPTIMAL:
        run.status = "OPTIMAL"
        ctx.message = "Optimal timetable found within the time budget."
    elif ctx.require_optimal:
        run.status = "SUBOPTIMAL"
        ctx.warnings.append(
            "A feasible timetable was found, but optimality was not proven (SUBOPTIMAL)."
        )
        db.add(
            TimetableConflict(
                tenant_id=tenant_id,
                run_id=run.id,
                severity="WARN",
                conflict_type="SUBOPTIMAL",
                message="Feasible timetable found but optimality not proven (time limit reached).",
                metadata_json={"max_time_seconds": float(ctx.max_time_seconds)},
            )
        )
        gap_info = (
            f" (objective={ctx.objective_score}, bound={ctx.best_objective_bound}, gap={ctx.optimality_gap})"
            if ctx.optimality_gap is not None
            else ""
        )
        ctx.message = (
            f"Solver found a valid timetable but optimality was not proven within the time budget."
            f" More time may produce a better timetable.{gap_info}"
        )
    else:
        run.status = "FEASIBLE"
        gap_info = (
            f" (objective={ctx.objective_score}, bound={ctx.best_objective_bound}, gap={ctx.optimality_gap})"
            if ctx.optimality_gap is not None
            else ""
        )
        ctx.message = (
            f"Solver found a valid timetable but optimality was not proven within the time budget."
            f" More time may produce a better timetable.{gap_info}"
        )
    run.solver_version = "cp-sat-v1"
    run.solve_time_seconds = ctx.solve_time_seconds
    run.total_variables = len(ctx.x) + len(ctx.z)
    # constraints: use solver num_constraints if available, else None
    if hasattr(solver, "NumBooleans"):
        run.total_constraints = None  # CP-SAT doesn't expose this directly
    run.objective_value = float(ctx.objective_score) if ctx.objective_score is not None else None
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
        entries_written=ctx.entries_written,
        conflicts=[],
        objective_score=ctx.objective_score,
        warnings=ctx.warnings,
        solver_stats=ctx.solver_stats,
        best_objective_bound=ctx.best_objective_bound,
        optimality_gap=ctx.optimality_gap,
        solve_time_seconds=ctx.solve_time_seconds,
        message=ctx.message,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _compute_warnings(ctx: SolverContext, solver: cp_model.CpSolver) -> None:
    try:
        for teacher_id, teacher in ctx.teacher_by_id.items():
            max_week = int(getattr(teacher, "max_per_week", 0) or 0)
            if max_week <= 0:
                continue
            used = 0
            for term in ctx.teacher_all_terms.get(teacher_id, []):
                if isinstance(term, int):
                    used += term
                else:
                    used += int(solver.Value(term))
            if used >= int(0.9 * max_week):
                ctx.warnings.append(
                    f"Teacher {getattr(teacher, 'code', teacher_id)} assigned {used}/{max_week} weekly load"
                )

        theory_room_capacity = len(ctx.rooms_by_type.get("CLASSROOM", [])) + len(
            ctx.rooms_by_type.get("LT", [])
        )
        lab_room_capacity = len(ctx.rooms_by_type.get("LAB", []))
        if theory_room_capacity > 0:
            max_used = 0
            for ts in ctx.slots:
                slot_id = ts.id
                used = int(ctx.special_theory_by_slot.get(slot_id, 0) or 0) + int(
                    ctx.fixed_theory_by_slot.get(slot_id, 0) or 0
                )
                for term in ctx.room_terms_by_slot.get(slot_id, []):
                    if isinstance(term, int):
                        used += term
                    else:
                        used += int(solver.Value(term))
                max_used = max(max_used, used)
            if max_used >= int(0.95 * theory_room_capacity):
                ctx.warnings.append(
                    f"Room utilization near capacity: max {max_used}/{theory_room_capacity} THEORY rooms used"
                )

        if lab_room_capacity > 0:
            max_used = 0
            for ts in ctx.slots:
                slot_id = ts.id
                used = int(ctx.special_lab_by_slot.get(slot_id, 0) or 0) + int(
                    ctx.fixed_lab_by_slot.get(slot_id, 0) or 0
                )
                for term in ctx.lab_room_terms_by_slot.get(slot_id, []):
                    if isinstance(term, int):
                        used += term
                    else:
                        used += int(solver.Value(term))
                max_used = max(max_used, used)
            if max_used >= int(0.95 * lab_room_capacity):
                ctx.warnings.append(
                    f"Room utilization near capacity: max {max_used}/{lab_room_capacity} LAB rooms used"
                )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to compute solver warnings", exc_info=True)
        ctx.warnings = []


def _compute_solver_stats(ctx: SolverContext, solver: cp_model.CpSolver, status: int) -> None:
    ctx.solver_stats = {
        "ortools_status": int(status),
        "wall_time_seconds": float(getattr(solver, "WallTime", lambda: 0.0)()),
        "num_branches": int(getattr(solver, "NumBranches", lambda: 0)()),
        "num_conflicts": int(getattr(solver, "NumConflicts", lambda: 0)()),
        "status_name": (
            {0: "UNKNOWN", 1: "MODEL_INVALID", 2: "FEASIBLE", 3: "INFEASIBLE", 4: "OPTIMAL"}.get(
                int(status), str(int(status))
            )
        ),
        "require_optimal": bool(ctx.require_optimal),
    }

    # Section gap metrics
    try:
        gap_pairs = 0
        gap_sum = 0
        max_gap = 0
        for (_sec_id, _day), occ_list in ctx.occ_by_section_day.items():
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
        ctx.solver_stats["section_gap_max_empty_slots"] = int(max_gap)
        ctx.solver_stats["section_gap_avg_empty_slots"] = (
            float(gap_sum / gap_pairs) if gap_pairs else 0.0
        )
        if ctx.internal_gap_terms:
            ctx.solver_stats["section_internal_gap_slots"] = int(
                sum(int(solver.Value(v)) for v in ctx.internal_gap_terms)
            )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to compute section gap stats", exc_info=True)

    # Teacher gap metrics
    try:
        teacher_gap_total = 0
        if ctx.teacher_gap_terms:
            teacher_gap_total = int(sum(int(solver.Value(v)) for v in ctx.teacher_gap_terms))
        ctx.solver_stats["teacher_internal_gap_slots"] = teacher_gap_total
    except Exception:
        pass

    # Subject day-spread violations
    try:
        spread_violations = 0
        if ctx.subject_spread_penalty_terms:
            spread_violations = int(
                sum(int(solver.Value(v)) for v in ctx.subject_spread_penalty_terms)
            )
        ctx.solver_stats["subject_spread_violations"] = spread_violations
    except Exception:
        pass

    # Daily load balance
    try:
        balance_sum = 0
        if ctx.daily_load_balance_terms:
            balance_sum = int(
                sum(int(solver.Value(v)) for v in ctx.daily_load_balance_terms)
            )
        ctx.solver_stats["daily_load_spread_total"] = balance_sum
    except Exception:
        pass

    # ── Composite quality score (0-100) ──────────────────────────────────
    _compute_quality_score(ctx)


def _compute_quality_score(ctx: SolverContext) -> None:
    """Compute a composite quality score (0-100) across multiple dimensions.

    Dimensions:
        1. Section compactness   — fewer student gaps = higher score
        2. Teacher compactness   — fewer teacher gaps = higher score
        3. Subject day-spread    — fewer same-day repeats = higher score
        4. Daily load balance    — more even daily loads = higher score
        5. Room conflicts        — fewer room conflicts = higher score

    Each dimension scores 0-100, final score is a weighted average.
    """
    try:
        scores: dict[str, float] = {}
        weights: dict[str, float] = {
            "section_compactness": 30.0,
            "teacher_compactness": 20.0,
            "subject_spread": 20.0,
            "daily_balance": 15.0,
            "room_fit": 15.0,
        }

        # 1. Section compactness: fewer internal gaps → higher score
        total_gaps = ctx.solver_stats.get("section_internal_gap_slots", 0)
        # Approximate maximum: 3 gaps/day * 6 days * num_sections
        max_possible_gaps = max(1, 3 * 6 * len(ctx.sections))
        scores["section_compactness"] = max(0.0, 100.0 * (1.0 - float(total_gaps) / max_possible_gaps))

        # 2. Teacher compactness
        teacher_gaps = ctx.solver_stats.get("teacher_internal_gap_slots", 0)
        max_teacher_gaps = max(1, 3 * 6 * len(ctx.teacher_by_id))
        scores["teacher_compactness"] = max(0.0, 100.0 * (1.0 - float(teacher_gaps) / max_teacher_gaps))

        # 3. Subject day-spread
        spread_violations = ctx.solver_stats.get("subject_spread_violations", 0)
        # Rough max: every subject pair could cluster on every day
        max_spread = max(1, len(ctx.sections) * 3)  # ~3 subjects might cluster
        scores["subject_spread"] = max(0.0, 100.0 * (1.0 - float(spread_violations) / max_spread))

        # 4. Daily balance
        # With N sessions spread over D days, minimum spread is 0 (if N % D == 0) or 1.
        # A realistic "bad" spread is ~5 per section; scale accordingly.
        balance_total = ctx.solver_stats.get("daily_load_spread_total", 0)
        # Use a gentler denominator: avg 2 spread per section is "acceptable"
        max_balance = max(1, len(ctx.sections) * 6)
        scores["daily_balance"] = max(0.0, min(100.0, 100.0 * (1.0 - float(balance_total) / max_balance)))

        # 5. Room fit: fraction without room conflicts
        total_entries = max(1, ctx.entries_written)
        room_conflicts = len(ctx.conflicting_special_room_slots) + len(ctx.conflicting_fixed_room_slots)
        scores["room_fit"] = max(0.0, 100.0 * (1.0 - float(room_conflicts) / total_entries))

        # Weighted average
        total_weight = sum(weights.values())
        quality_score = sum(scores[k] * weights[k] for k in weights) / total_weight

        ctx.solver_stats["quality_score"] = round(quality_score, 1)
        ctx.solver_stats["quality_breakdown"] = {k: round(v, 1) for k, v in scores.items()}

    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to compute quality score", exc_info=True)


def _make_entry(ctx: SolverContext, **kwargs: Any) -> TimetableEntry:
    """Create a TimetableEntry, run invariant checks, add to DB, increment counter.

    OPTIMIZATION (Task 6): each written entry is indexed into
    ctx.entries_by_slot[slot_id] as it is created.  This gives O(1)
    slot lookups for any post-write conflict or utilisation analysis,
    avoiding an O(E²) pairwise scan over all output entries.
    """
    entry = TimetableEntry(**kwargs)
    assert_entry_invariants(ctx, entry)
    ctx.db.add(entry)
    ctx.entries_written += 1
    # O(E) slot index — populated once per entry, free to query later.
    ctx.entries_by_slot[entry.slot_id].append(entry)
    return entry


def _write_special_entries(ctx: SolverContext) -> None:
    run = ctx.run
    tenant_id = ctx.tenant_id
    for sec_id, subj_id, teacher_id, room_id, slot_id in ctx.special_entries_to_write:
        combined_conflict_id = None
        if (str(sec_id), str(slot_id)) in ctx.conflicting_special_room_slots:
            combined_conflict_id = room_conflict_group_id(
                run_id=run.id, room_id=room_id, slot_id=slot_id
            )
        _make_entry(
            ctx,
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )


def _write_fixed_entries(ctx: SolverContext) -> None:
    run = ctx.run
    tenant_id = ctx.tenant_id
    for sec_id, subj_id, teacher_id, room_id, slot_id in ctx.fixed_entries_to_write:
        combined_conflict_id = None
        if (str(sec_id), str(slot_id)) in ctx.conflicting_fixed_room_slots:
            combined_conflict_id = room_conflict_group_id(
                run_id=run.id, room_id=room_id, slot_id=slot_id
            )
        _make_entry(
            ctx,
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )


def _write_theory_entries(ctx: SolverContext, solver: cp_model.CpSolver) -> None:
    run = ctx.run
    tenant_id = ctx.tenant_id
    for (sec_id, subj_id, slot_id), xv in ctx.x.items():
        if solver.Value(xv) != 1:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        teacher_id = ctx.assigned_teacher_by_section_subject.get((sec_id, subj_id))
        if teacher_id is None or subj is None:
            continue
        fixed_room = ctx.fixed_room_by_section_slot.get((sec_id, slot_id))
        if fixed_room is not None:
            room_id, ok_room = fixed_room, True
        else:
            room_id, ok_room = pick_room(ctx, slot_id, str(subj.subject_type), section_id=sec_id, subject_id=subj_id)
        if room_id is None:
            continue

        combined_conflict_id = None
        if fixed_room is not None and (str(sec_id), str(slot_id)) in ctx.conflicting_fixed_room_slots:
            combined_conflict_id = room_conflict_group_id(
                run_id=run.id, room_id=room_id, slot_id=slot_id
            )
        elif not ok_room:
            combined_conflict_id = room_conflict_group_id(
                run_id=run.id, room_id=room_id, slot_id=slot_id
            )

        if not ok_room:
            ctx.db.add(
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
        _make_entry(
            ctx,
            tenant_id=tenant_id,
            run_id=run.id,
            academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
            section_id=sec_id,
            subject_id=subj_id,
            teacher_id=teacher_id,
            room_id=room_id,
            slot_id=slot_id,
            combined_class_id=combined_conflict_id,
        )


def _emit_block_batch_occurrence(ctx: SolverContext, block_id: Any, batch_idx: int, slot_id: Any) -> None:
    """Emit timetable entries for one elective batch occurrence."""
    run = ctx.run
    tenant_id = ctx.tenant_id
    pairs = ctx.block_subject_pairs_by_block.get(block_id, [])
    batch_sections = ctx.elective_batches_by_block.get(block_id, [])
    sec_ids = batch_sections[batch_idx] if 0 <= int(batch_idx) < len(batch_sections) else []
    if not pairs:
        return
    if not sec_ids:
        return

    from solver.room_assigner import _sid, _rid

    for subj_id, teacher_id in pairs:
        forced = ctx.forced_room_by_block_batch_subject_slot.get((block_id, int(batch_idx), subj_id, slot_id))
        if forced is not None:
            sid = _sid(slot_id)
            rid = _rid(forced)
            ok_room = rid not in ctx.used_rooms_by_slot[sid]
            ctx.used_rooms_by_slot[sid].add(rid)
            if (not ok_room) and getattr(settings, "solver_strict_mode", False):
                raise SolverInvariantError(
                    "NO_ROOM_AVAILABLE",
                    "Forced elective room is already occupied in this slot.",
                    details={"slot_id": str(slot_id), "room_id": str(forced), "run_id": str(run.id)},
                )
            room_id = forced
        else:
            room_id, ok_room = pick_lt_room(ctx, slot_id)
            if room_id is None:
                continue

        combined_conflict_id = elective_group_id(
            run_id=run.id,
            block_id=f"{block_id}:{int(batch_idx)}",
            subject_id=subj_id,
            slot_id=slot_id,
        )
        if not ok_room:
            combined_conflict_id = room_conflict_group_id(
                run_id=run.id, room_id=room_id, slot_id=slot_id
            )
            ctx.db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="NO_LT_ROOM_AVAILABLE",
                    message="No free LT room for elective block slot; assigned a conflicting LT.",
                    section_id=sec_ids[0],
                    subject_id=subj_id,
                    teacher_id=teacher_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={"elective_block_id": str(block_id), "batch_idx": int(batch_idx)},
                )
            )

        for sec_id in sec_ids:
            _make_entry(
                ctx,
                tenant_id=tenant_id,
                run_id=run.id,
                academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
                section_id=sec_id,
                subject_id=subj_id,
                teacher_id=teacher_id,
                room_id=room_id,
                slot_id=slot_id,
                combined_class_id=combined_conflict_id,
                elective_block_id=block_id,
            )


def _write_elective_block_entries(ctx: SolverContext, solver: cp_model.CpSolver) -> None:
    # Emit locked batch-level block occurrences first.
    for block_id, batch_idx, slot_id in sorted(
        list(ctx.locked_elective_block_batch_slots), key=lambda x: (str(x[0]), int(x[1]), str(x[2]))
    ):
        _emit_block_batch_occurrence(ctx, block_id, int(batch_idx), slot_id)

    # Emit solver-chosen batch-level block occurrences.
    for (block_id, batch_idx, slot_id), zv in ctx.z.items():
        if solver.Value(zv) != 1:
            continue
        _emit_block_batch_occurrence(ctx, block_id, int(batch_idx), slot_id)


def _write_combined_theory_entries(ctx: SolverContext, solver: cp_model.CpSolver) -> None:
    run = ctx.run
    tenant_id = ctx.tenant_id
    for (group_id, slot_id), gv in ctx.combined_x.items():
        if solver.Value(gv) != 1:
            continue

        subj_id = ctx.group_subject.get(group_id)
        if subj_id is None:
            continue

        chosen_t = ctx.effective_teacher_by_gid.get(group_id)
        if chosen_t is None:
            for sec_id in ctx.group_sections.get(group_id, []):
                tid = ctx.assigned_teacher_by_section_subject.get((sec_id, subj_id))
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

        fixed_rooms = [
            ctx.fixed_room_by_section_slot.get((sid, slot_id))
            for sid in ctx.group_sections.get(group_id, [])
        ]
        fixed_rooms = [r for r in fixed_rooms if r is not None]
        if fixed_rooms:
            room_id, ok_room = fixed_rooms[0], True
        else:
            room_id, ok_room = pick_lt_room(ctx, slot_id)
        if room_id is None:
            continue
        if not ok_room:
            ctx.db.add(
                TimetableConflict(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    severity="WARN",
                    conflict_type="NO_LT_ROOM_AVAILABLE",
                    message="No free LT room for combined class slot; assigned a conflicting LT.",
                    section_id=ctx.group_sections.get(group_id, [None])[0],
                    subject_id=subj_id,
                    room_id=room_id,
                    slot_id=slot_id,
                    metadata_json={"combined_group_id": str(group_id)},
                )
            )

        for sec_id in ctx.group_sections.get(group_id, []):
            _make_entry(
                ctx,
                tenant_id=tenant_id,
                run_id=run.id,
                academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
                section_id=sec_id,
                subject_id=subj_id,
                teacher_id=chosen_t,
                room_id=ctx.fixed_room_by_section_slot.get((sec_id, slot_id)) or room_id,
                slot_id=slot_id,
                combined_class_id=group_id,
            )


def _write_lab_entries(ctx: SolverContext, solver: cp_model.CpSolver) -> None:
    run = ctx.run
    tenant_id = ctx.tenant_id
    for (sec_id, subj_id, day, start_idx), sv in ctx.lab_start.items():
        if solver.Value(sv) != 1:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is None:
            continue
        block = ctx.lab_block_for(subj_id)
        if block < 1:
            block = 1
        chosen_t = ctx.assigned_teacher_by_section_subject.get((sec_id, subj_id))
        if chosen_t is None:
            continue

        block_slots = []
        for j in range(block):
            ts = ctx.slot_by_day_index.get((day, start_idx + j))
            if ts is not None:
                block_slots.append(ts)

        slot_ids = [str(ts.id) for ts in block_slots]
        if not slot_ids:
            continue

        fixed_rooms = [ctx.fixed_room_by_section_slot.get((sec_id, sid)) for sid in slot_ids]
        fixed_rooms = [r for r in fixed_rooms if r is not None]
        if fixed_rooms:
            room_id, ok_room = fixed_rooms[0], True
        else:
            room_id, ok_room = pick_room_for_block(ctx, slot_ids, subject_id=subj_id)
        if room_id is None:
            continue

        combined_conflict_id = (
            None
            if ok_room
            else room_conflict_group_id(run_id=run.id, room_id=room_id, slot_id=str(slot_ids[0]))
        )

        for j in range(block):
            ts = ctx.slot_by_day_index.get((day, start_idx + j))
            if ts is None:
                continue
            if not ok_room:
                ctx.db.add(
                    TimetableConflict(
                        tenant_id=tenant_id,
                        run_id=run.id,
                        severity="WARN",
                        conflict_type="NO_ROOM_AVAILABLE",
                        message="No single lab room for the full lab block; assigned a conflicting room.",
                        section_id=sec_id,
                        subject_id=subj_id,
                        room_id=room_id,
                        slot_id=ts.id,
                        metadata_json={"subject_type": "LAB"},
                    )
                )
            _make_entry(
                ctx,
                tenant_id=tenant_id,
                run_id=run.id,
                academic_year_id=ctx.section_year_by_id.get(sec_id) or run.academic_year_id,
                section_id=sec_id,
                subject_id=subj_id,
                teacher_id=chosen_t,
                room_id=room_id,
                slot_id=ts.id,
                combined_class_id=combined_conflict_id,
            )
