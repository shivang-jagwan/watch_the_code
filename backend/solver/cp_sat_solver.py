"""CP-SAT-based timetable solver — orchestrator.

Public API (backward-compatible):
    solve_program_year(...)  -> SolveResult
    solve_program_global(...) -> SolveResult
    SolverInvariantError
    SolveResult

The heavy lifting is split into sub-modules:
    context        — SolverContext dataclass (shared state)
    data_loader    — database queries
    pre_solve_locks— special allotments / fixed entries pre-processing
    variables      — CP-SAT BoolVar creation
    constraints    — hard & soft constraints
    objective      — objective function
    room_assigner  — greedy post-solve room assignment
    result_writer  — write TimetableEntry rows + commit
"""

from __future__ import annotations

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from models.timetable_conflict import TimetableConflict
from models.timetable_run import TimetableRun

# Re-export for backward compatibility
from solver.context import SolveResult, SolverContext, SolverInvariantError  # noqa: F401

from solver.constraints import add_constraints
from solver.data_loader import load_all
from solver.objective import add_objective
from solver.pre_solve_locks import apply_pre_solve_locks
from solver.result_writer import write_results
from solver.variables import create_variables


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
    """Program-wide solve across all academic years."""
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

    # 1. Build context
    ctx = SolverContext(
        db=db,
        run=run,
        program_id=program_id,
        academic_year_id=academic_year_id,
        seed=seed,
        max_time_seconds=max_time_seconds,
        enforce_teacher_load_limits=enforce_teacher_load_limits,
        require_optimal=require_optimal,
        tenant_id=tenant_id,
    )

    # 2. Load data
    load_all(ctx)

    # 3. Pre-solve locks (special allotments, fixed entries, teacher pruning)
    apply_pre_solve_locks(ctx)

    # 4. Create CP-SAT variables
    create_variables(ctx)

    # 5. Add constraints
    add_constraints(ctx)

    # 6. Set objective
    add_objective(ctx)

    # 7. Search strategy hints — guide CP-SAT to branch on the most
    #    constrained decision variables first (section-slot assignments).
    _add_search_hints(ctx)

    # 8. Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time_seconds)
    solver.parameters.num_search_workers = 8
    # Improve solution quality: allow solver to keep searching for better solutions
    solver.parameters.linearization_level = 1
    solver.parameters.log_search_progress = False
    if seed is not None:
        solver.parameters.random_seed = int(seed)

    status = solver.Solve(ctx.model)

    # 9. Handle infeasible / error
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _handle_infeasible(ctx, solver, status)

    # 10. Write results
    return write_results(ctx, solver, status)


def _add_search_hints(ctx: SolverContext) -> None:
    """Add decision strategy hints to guide CP-SAT branching.

    Hints the solver to:
    1. Branch on section-slot variables first (most constrained)
    2. Prefer assigning variables to 1 (commit early)

    This typically improves time-to-first-solution significantly.
    """
    # Collect all primary decision variables
    hint_vars = []

    # Theory variables — most constrained first
    hint_vars.extend(ctx.x.values())

    # Lab start variables
    hint_vars.extend(ctx.lab_start.values())

    # Combined THEORY variables
    hint_vars.extend(ctx.combined_x.values())

    # Elective block variables
    hint_vars.extend(ctx.z.values())

    if hint_vars:
        ctx.model.AddDecisionStrategy(
            hint_vars,
            cp_model.CHOOSE_FIRST,          # pick first unassigned var
            cp_model.SELECT_MAX_VALUE,       # try value 1 first (commit)
        )


def _handle_infeasible(
    ctx: SolverContext,
    solver: cp_model.CpSolver,
    status: int,
) -> SolveResult:
    """Handle non-feasible solver outcomes."""
    ortools_status = int(status)
    diagnostics: list[dict] = []
    reason_summary: str | None = None
    tenant_id = ctx.tenant_id
    run = ctx.run

    if status == cp_model.INFEASIBLE:
        run.status = "INFEASIBLE"
        conflict_type = "INFEASIBLE"
        message = (
            "Solver infeasible due to special locked allotments."
            if ctx.special_allotments
            else "Solver could not find a feasible timetable."
        )

        try:
            from solver.solver_diagnostics import run_infeasibility_analysis, summarize_diagnostics

            diagnostics = run_infeasibility_analysis(
                {
                    "sections": ctx.sections,
                    "section_required": ctx.section_required,
                    "assigned_teacher_by_section_subject": ctx.assigned_teacher_by_section_subject,
                    "subject_by_id": ctx.subject_by_id,
                    "teacher_by_id": ctx.teacher_by_id,
                    "slots": ctx.slots,
                    "slot_info": ctx.slot_info,
                    "slot_by_day_index": ctx.slot_by_day_index,
                    "windows_by_section": ctx.windows_by_section,
                    "fixed_entries": ctx.fixed_entries,
                    "special_allotments": ctx.special_allotments,
                    "group_sections": ctx.group_sections,
                    "group_subject": ctx.group_subject,
                    "blocks_by_section": ctx.blocks_by_section,
                    "block_subject_pairs_by_block": ctx.block_subject_pairs_by_block,
                    "rooms_by_type": ctx.rooms_by_type,
                    "room_by_id": ctx.room_by_id,
                }
            )
            reason_summary = summarize_diagnostics(diagnostics)
        except Exception:
            diagnostics = []
            reason_summary = None
    elif status == cp_model.UNKNOWN:
        run.status = "ERROR"
        conflict_type = "TIMEOUT"
        message = (
            "Solver timed out without finding a feasible timetable. "
            "Increase max_time_seconds or relax constraints."
        )
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
    ctx.db.add(conflict)
    ctx.db.commit()
    return SolveResult(
        status=str(run.status),
        entries_written=0,
        conflicts=[conflict],
        diagnostics=diagnostics,
        reason_summary=reason_summary,
    )
