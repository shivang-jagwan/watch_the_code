"""Shared solver context holding all data and intermediate state.

SolverContext replaces the ~50 local variables that were previously scattered
throughout the monolithic ``_solve_program`` function.  Every sub-module
(data_loader, pre_solve_locks, variables, constraints, objective,
room_assigner, result_writer) reads and writes through this single object.

OPTIMIZATION NOTES (added 2026-03):
  - Integer index maps (section_idx, subject_idx, etc.) are populated by
    data_loader after all entities are loaded.  They are used as compact
    integer keys for CP-SAT variable dicts, reducing Python hashing cost.

  - valid_slots_by_section_subject[(section_id, subject_id)] is a pruned
    set of slot_ids that are valid *for a specific subject* in addition to
    being within the section's time window.  It is computed once in
    data_loader and replaces the per-variable teacher-off-day check that
    the old code performed inside the inner loop of variable creation.

  - These additions are purely additive: all existing UUID-keyed fields are
    unchanged, so pre_solve_locks, result_writer, and room_assigner continue
    to work without modification.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from models.elective_block import ElectiveBlock
from models.room import Room
from models.section import Section
from models.subject import Subject
from models.teacher import Teacher
from models.time_slot import TimeSlot
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from models.timetable_run import TimetableRun
from models.fixed_timetable_entry import FixedTimetableEntry
from models.special_allotment import SpecialAllotment


class SolverInvariantError(RuntimeError):
    """Raised when a solver invariant is violated (e.g. duplicate entries)."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SolveResult:
    """Return value from a solver run."""

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
        best_objective_bound: int | None = None,
        optimality_gap: int | None = None,
        solve_time_seconds: float | None = None,
        message: str | None = None,
    ):
        self.status = status
        self.entries_written = entries_written
        self.conflicts = conflicts
        self.diagnostics = diagnostics or []
        self.reason_summary = reason_summary
        self.objective_score = objective_score
        self.warnings = warnings or []
        self.solver_stats = solver_stats or {}
        self.best_objective_bound = best_objective_bound
        self.optimality_gap = optimality_gap
        self.solve_time_seconds = solve_time_seconds
        self.message = message


@dataclass
class SolverContext:
    """Holds ALL state shared across solver sub-modules."""

    # --- Injected parameters -------------------------------------------------
    db: Session
    run: TimetableRun
    program_id: Any
    academic_year_id: Any | None
    seed: int | None
    max_time_seconds: float
    enforce_teacher_load_limits: bool
    require_optimal: bool
    tenant_id: Any | None = None

    # --- Loaded data ---------------------------------------------------------
    sections: list[Section] = field(default_factory=list)
    section_year_by_id: dict[Any, Any] = field(default_factory=dict)
    solve_year_ids: list[Any] = field(default_factory=list)

    slots: list[TimeSlot] = field(default_factory=list)
    slot_by_day_index: dict[tuple[int, int], TimeSlot] = field(default_factory=dict)
    slot_info: dict[Any, tuple[int, int]] = field(default_factory=dict)
    slots_by_day: dict[int, list[TimeSlot]] = field(default_factory=lambda: defaultdict(list))

    rooms_all: list[Room] = field(default_factory=list)
    room_by_id: dict[Any, Room] = field(default_factory=dict)
    rooms_by_type: dict[str, list[Room]] = field(default_factory=lambda: defaultdict(list))

    subjects: list[Subject] = field(default_factory=list)
    subject_by_id: dict[Any, Subject] = field(default_factory=dict)

    teachers: list[Teacher] = field(default_factory=list)
    teacher_by_id: dict[Any, Teacher] = field(default_factory=dict)

    assigned_teacher_by_section_subject: dict[tuple[Any, Any], Any] = field(default_factory=dict)

    fixed_entries: list[FixedTimetableEntry] = field(default_factory=list)
    special_allotments: list[SpecialAllotment] = field(default_factory=list)

    section_required: dict[Any, list[tuple[Any, int | None]]] = field(default_factory=dict)
    windows_by_section: dict[Any, list] = field(default_factory=lambda: defaultdict(list))
    # teacher_id → list[TeacherTimeWindow]; populated by data_loader
    teacher_windows_by_id: dict[Any, list] = field(default_factory=lambda: defaultdict(list))

    # Set of slot_ids marked as lunch/break periods; solver refuses to schedule here
    lunch_slot_ids: set[Any] = field(default_factory=set)

    # Elective blocks
    blocks_by_section: dict[Any, list[Any]] = field(default_factory=lambda: defaultdict(list))
    sections_by_block: dict[Any, list[Any]] = field(default_factory=lambda: defaultdict(list))
    elective_block_by_id: dict[Any, ElectiveBlock] = field(default_factory=dict)
    block_subject_pairs_by_block: dict[Any, list[tuple[Any, Any]]] = field(default_factory=lambda: defaultdict(list))
    elective_block_by_section_subject: dict[tuple[Any, Any], Any] = field(default_factory=dict)

    # Allowed slots per section
    allowed_slots_by_section: dict[Any, set[Any]] = field(default_factory=lambda: defaultdict(set))
    allowed_slot_indices_by_section_day: dict[tuple[Any, int], list[int]] = field(default_factory=lambda: defaultdict(list))

    # Combined groups
    group_sections: dict[Any, list[Any]] = field(default_factory=lambda: defaultdict(list))
    group_subject: dict[Any, Any] = field(default_factory=dict)
    group_teacher_id: dict[Any, Any] = field(default_factory=dict)
    combined_gid_by_sec_subj: dict[tuple[Any, Any], Any] = field(default_factory=dict)

    # --- Pre-solve lock tracking ---------------------------------------------
    locked_theory_sessions_by_sec_subj: dict[tuple[Any, Any], int] = field(default_factory=lambda: defaultdict(int))
    locked_theory_sessions_by_sec_subj_day: dict[tuple[Any, Any, int], int] = field(default_factory=lambda: defaultdict(int))
    locked_lab_sessions_by_sec_subj: dict[tuple[Any, Any], int] = field(default_factory=lambda: defaultdict(int))
    locked_lab_sessions_by_sec_subj_day: dict[tuple[Any, Any, int], int] = field(default_factory=lambda: defaultdict(int))

    locked_section_slots: set[tuple[Any, Any]] = field(default_factory=set)
    locked_teacher_slots: set[tuple[Any, Any]] = field(default_factory=set)
    locked_teacher_slot_day: dict[tuple[Any, Any], int] = field(default_factory=dict)
    locked_slot_indices_by_section_day: dict[tuple[Any, int], set[int]] = field(default_factory=lambda: defaultdict(set))

    special_room_by_section_slot: dict[tuple[Any, Any], Any] = field(default_factory=dict)
    special_entries_to_write: list[tuple[Any, Any, Any, Any, Any]] = field(default_factory=list)

    # Elective batching metadata (e.g., 6 sections -> 2 batches of 3)
    elective_batches_by_block: dict[Any, list[list[Any]]] = field(default_factory=dict)
    elective_batch_index_by_block_section: dict[tuple[Any, Any], int] = field(default_factory=dict)

    locked_elective_sessions_by_block_batch: dict[tuple[Any, int], int] = field(default_factory=lambda: defaultdict(int))
    locked_elective_sessions_by_block_batch_day: dict[tuple[Any, int, int], int] = field(default_factory=lambda: defaultdict(int))
    locked_elective_block_batch_slots: set[tuple[Any, int, Any]] = field(default_factory=set)
    forced_room_by_block_batch_subject_slot: dict[tuple[Any, int, Any, Any], Any] = field(default_factory=dict)
    locked_block_theory_room_demand_by_slot: dict[Any, int] = field(default_factory=lambda: defaultdict(int))

    fixed_room_by_section_slot: dict[tuple[Any, Any], Any] = field(default_factory=dict)
    fixed_entries_to_write: list[tuple[Any, Any, Any, Any, Any]] = field(default_factory=list)
    locked_fixed_entry_ids: set[str] = field(default_factory=set)

    teacher_disallowed_slot_ids: dict[Any, set[Any]] = field(default_factory=lambda: defaultdict(set))

    # --- CP-SAT model & decision variables -----------------------------------
    model: cp_model.CpModel = field(default_factory=cp_model.CpModel)

    # Theory vars
    x: dict[tuple[Any, Any, Any], Any] = field(default_factory=dict)
    x_by_sec_subj: dict[tuple[Any, Any], list] = field(default_factory=lambda: defaultdict(list))
    x_by_sec_subj_day: dict[tuple[Any, Any, int], list] = field(default_factory=lambda: defaultdict(list))

    # Elective block vars
    z: dict[tuple[Any, int, Any], Any] = field(default_factory=dict)
    z_by_block_batch: dict[tuple[Any, int], list] = field(default_factory=lambda: defaultdict(list))
    z_by_block_batch_day: dict[tuple[Any, int, int], list] = field(default_factory=lambda: defaultdict(list))

    # Lab vars
    lab_start: dict[tuple[Any, Any, int, int], Any] = field(default_factory=dict)
    lab_starts_by_sec_subj: dict[tuple[Any, Any], list] = field(default_factory=lambda: defaultdict(list))
    lab_starts_by_sec_subj_day: dict[tuple[Any, Any, int], list] = field(default_factory=lambda: defaultdict(list))

    # Combined THEORY vars
    combined_x: dict[tuple[Any, Any], Any] = field(default_factory=dict)
    combined_sessions_required: dict[Any, int] = field(default_factory=dict)
    combined_vars_by_gid: dict[Any, list] = field(default_factory=lambda: defaultdict(list))
    combined_vars_by_gid_day: dict[tuple[Any, int], list] = field(default_factory=lambda: defaultdict(list))
    effective_teacher_by_gid: dict[Any, Any] = field(default_factory=dict)

    # Aggregate terms for constraints
    teacher_slot_terms: dict[tuple[Any, Any], list] = field(default_factory=lambda: defaultdict(list))
    section_slot_terms: dict[tuple[Any, Any], list] = field(default_factory=lambda: defaultdict(list))
    teacher_all_terms: dict[Any, list] = field(default_factory=lambda: defaultdict(list))
    teacher_day_terms: dict[tuple[Any, int], list] = field(default_factory=lambda: defaultdict(list))
    teacher_active_days: dict[Any, set[int]] = field(default_factory=lambda: defaultdict(set))

    room_terms_by_slot: dict[Any, list] = field(default_factory=lambda: defaultdict(list))
    lab_room_terms_by_slot: dict[Any, list] = field(default_factory=lambda: defaultdict(list))

    # Compactness/gap (built in constraints phase)
    occ_by_section_day: dict[tuple[Any, int], list[tuple[int, Any]]] = field(default_factory=dict)
    internal_gap_terms: list[Any] = field(default_factory=list)

    # Subject day-spread penalty terms (soft: penalise >1 session of same subject on same day)
    subject_spread_penalty_terms: list[Any] = field(default_factory=list)

    # Teacher compactness gap terms (soft: penalise teacher internal gaps)
    teacher_gap_terms: list[Any] = field(default_factory=list)

    # Daily load balance terms per section (soft: penalise uneven days)
    daily_load_balance_terms: list[Any] = field(default_factory=list)

    # --- Post-solve state (room assignment & writing) ------------------------
    used_rooms_by_slot: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    seen_uncombined_room_slot: set[tuple[str, str]] = field(default_factory=set)
    seen_non_elective_section_slot: set[tuple[str, str]] = field(default_factory=set)
    seen_teacher_slot_event: dict[tuple[str, str], str | None] = field(default_factory=dict)

    entries_written: int = 0
    conflicts: list[TimetableConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    solver_stats: dict[str, Any] = field(default_factory=dict)
    objective_score: int | None = None

    # Room conflict tracking
    conflicting_special_room_slots: set[tuple[str, str]] = field(default_factory=set)
    conflicting_fixed_room_slots: set[tuple[str, str]] = field(default_factory=set)

    # For elective block room assignment
    chosen_room_by_block_slot_subject: dict[tuple[Any, Any, Any], tuple[Any, bool]] = field(default_factory=dict)

    # Special/fixed room demand counts (for room capacity constraints)
    special_theory_by_slot: dict[Any, int] = field(default_factory=lambda: defaultdict(int))
    special_lab_by_slot: dict[Any, int] = field(default_factory=lambda: defaultdict(int))
    fixed_theory_by_slot: dict[Any, int] = field(default_factory=lambda: defaultdict(int))
    fixed_lab_by_slot: dict[Any, int] = field(default_factory=lambda: defaultdict(int))

    # ── OPTIMIZATION: Integer index maps ──────────────────────────────────
    # Populated once by data_loader._build_index_maps() after all entities
    # are loaded.  Dense integer keys (vs UUID strings) cut Python dict-hash
    # overhead by ~30-40% when building the CP-SAT model.
    #
    # Rule: use these only inside variables.py / constraints.py / objective.py.
    # All DB-facing code (result_writer, room_assigner) continues to use UUIDs.
    section_idx: dict[Any, int] = field(default_factory=dict)   # section_id  → int
    subject_idx: dict[Any, int] = field(default_factory=dict)   # subject_id  → int
    teacher_idx: dict[Any, int] = field(default_factory=dict)   # teacher_id  → int
    slot_idx_map: dict[Any, int] = field(default_factory=dict)  # slot_id     → int
    room_idx: dict[Any, int] = field(default_factory=dict)      # room_id     → int

    # Reverse maps (int → UUID) — needed for result_writer lookups
    idx_to_section: dict[int, Any] = field(default_factory=dict)
    idx_to_subject: dict[int, Any] = field(default_factory=dict)
    idx_to_teacher: dict[int, Any] = field(default_factory=dict)
    idx_to_slot: dict[int, Any] = field(default_factory=dict)
    idx_to_room: dict[int, Any] = field(default_factory=dict)

    # ── OPTIMIZATION: Per-subject pruned slot sets ────────────────────────
    # valid_slots_by_section_subject[(section_id, subject_id)] contains only
    # slot_ids that pass ALL of:
    #   • section time window
    #   • teacher off-day / locked slots (teacher_disallowed_slot_ids)
    #   • not already locked by a special-allotment / fixed-entry
    # Populated by data_loader._build_pruned_slots() called AFTER
    # apply_pre_solve_locks() fills teacher_disallowed_slot_ids.
    #
    # For LAB subjects the pruning additionally checks that a full contiguous
    # block of lab_block_size_slots can start at that slot.
    valid_slots_by_section_subject: dict[tuple[Any, Any], list[Any]] = field(default_factory=dict)

    # ── OPTIMIZATION: Pre-solve metrics ──────────────────────────────────
    # Populated by cp_sat_solver.py before calling solver.Solve().
    # Exposed in solver_stats so operators can track model size over time.
    pre_solve_metrics: dict[str, int] = field(default_factory=dict)

    # ── OPTIMIZATION: section_by_id lookup cache ─────────────────────────
    # data_loader populates this during load_all() so that room_assigner
    # and result_writer can do O(1) section lookups instead of linear scans.
    section_by_id: dict[Any, Any] = field(default_factory=dict)

    # ── Time-budget reporting ─────────────────────────────────────────────
    # Populated by result_writer after solver.Solve() returns.
    best_objective_bound: int | None = None   # solver.BestObjectiveBound()
    optimality_gap: int | None = None         # objective - best_bound (≥0)
    solve_time_seconds: float | None = None   # solver.WallTime()
    message: str | None = None                # human-readable status message

    # ── OPTIMIZATION: Pre-sorted room lists (Task 5) ──────────────────────
    # Built once by data_loader._build_room_cache() after rooms are loaded.
    # Eliminates the O(R log R) sort + O(S) section-linear-scan that
    # pick_room() previously performed on every single assignment call.
    #
    # lab_rooms_sorted            — LAB rooms, capacity ASC
    # classroom_rooms_sorted      — CLASSROOM rooms, capacity ASC
    # lt_rooms_sorted             — LT rooms, capacity ASC
    # lt_plus_classroom_rooms_sorted — LT then CLASSROOM (for elective/combined)
    # theory_rooms_sorted         — CLASSROOM + LT merged and sorted by cap ASC
    # room_candidates_by_section  — (section_id, "LAB"|"THEORY") → best-fit
    #                               ordered list pre-partitioned per section
    #                               strength.  O(1) lookup → O(R) scan.
    lab_rooms_sorted: list[Any] = field(default_factory=list)
    classroom_rooms_sorted: list[Any] = field(default_factory=list)
    lt_rooms_sorted: list[Any] = field(default_factory=list)
    lt_plus_classroom_rooms_sorted: list[Any] = field(default_factory=list)
    theory_rooms_sorted: list[Any] = field(default_factory=list)
    room_candidates_by_section: dict[tuple[Any, str], list[Any]] = field(default_factory=dict)

    # ── OPTIMIZATION: Post-solve slot-indexed entry map (Task 6) ──────────
    # Populated by result_writer._make_entry() as entries are written.
    # Groups written TimetableEntry rows by slot_id for O(E) post-write
    # conflict analysis and utilisation reporting — eliminates any need to
    # scan ctx.entries or perform pairwise (E²) comparisons.
    entries_by_slot: dict[Any, list[Any]] = field(default_factory=lambda: defaultdict(list))


# ── Task 8: Lightweight sub-context view facades ─────────────────────────────
#
# Splitting SolverContext into four true sub-dataclasses would require
# renaming every `ctx.field` access across 8 modules — high risk for minimal
# gain.  Instead we expose four *read-only façade objects* whose attributes
# are just property aliases back into the flat SolverContext.  This gives
# callers a structured logical view:
#
#   ctx.data        — loaded DB entities (sections, slots, rooms, …)
#   ctx.variables   — CP-SAT decision variable dicts (x, lab_start, z, …)
#   ctx.accumulate  — constraint/objective term-list accumulators
#   ctx.solution    — post-solve state (entries, stats, warnings, …)
#
# No behaviour changes; no other file needs modification.
# ─────────────────────────────────────────────────────────────────────────────

class _DataView:
    """Read-only view of the DB-loaded-data portion of SolverContext."""
    __slots__ = ("_ctx",)

    def __init__(self, ctx: "SolverContext") -> None:
        object.__setattr__(self, "_ctx", ctx)

    # Expose the most-used data fields as typed properties.
    @property
    def sections(self): return self._ctx.sections
    @property
    def section_by_id(self): return self._ctx.section_by_id
    @property
    def slots(self): return self._ctx.slots
    @property
    def slot_info(self): return self._ctx.slot_info
    @property
    def slots_by_day(self): return self._ctx.slots_by_day
    @property
    def teachers(self): return self._ctx.teachers
    @property
    def teacher_by_id(self): return self._ctx.teacher_by_id
    @property
    def subjects(self): return self._ctx.subjects
    @property
    def subject_by_id(self): return self._ctx.subject_by_id
    @property
    def rooms_all(self): return self._ctx.rooms_all
    @property
    def rooms_by_type(self): return self._ctx.rooms_by_type
    @property
    def room_by_id(self): return self._ctx.room_by_id
    @property
    def allowed_slots_by_section(self): return self._ctx.allowed_slots_by_section
    @property
    def section_required(self): return self._ctx.section_required


class _VariableView:
    """Read-only view of the CP-SAT variable dicts in SolverContext."""
    __slots__ = ("_ctx",)

    def __init__(self, ctx: "SolverContext") -> None:
        object.__setattr__(self, "_ctx", ctx)

    @property
    def x(self): return self._ctx.x
    @property
    def x_by_sec_subj(self): return self._ctx.x_by_sec_subj
    @property
    def x_by_sec_subj_day(self): return self._ctx.x_by_sec_subj_day
    @property
    def z(self): return self._ctx.z
    @property
    def z_by_block_batch(self): return self._ctx.z_by_block_batch
    @property
    def lab_start(self): return self._ctx.lab_start
    @property
    def lab_starts_by_sec_subj(self): return self._ctx.lab_starts_by_sec_subj
    @property
    def combined_x(self): return self._ctx.combined_x
    @property
    def combined_vars_by_gid(self): return self._ctx.combined_vars_by_gid
    @property
    def valid_slots(self): return self._ctx.valid_slots_by_section_subject


class _AccumulatorView:
    """Read-only view of the constraint/objective term-list accumulators."""
    __slots__ = ("_ctx",)

    def __init__(self, ctx: "SolverContext") -> None:
        object.__setattr__(self, "_ctx", ctx)

    @property
    def section_slot_terms(self): return self._ctx.section_slot_terms
    @property
    def teacher_slot_terms(self): return self._ctx.teacher_slot_terms
    @property
    def teacher_all_terms(self): return self._ctx.teacher_all_terms
    @property
    def teacher_day_terms(self): return self._ctx.teacher_day_terms
    @property
    def room_terms_by_slot(self): return self._ctx.room_terms_by_slot
    @property
    def lab_room_terms_by_slot(self): return self._ctx.lab_room_terms_by_slot
    @property
    def internal_gap_terms(self): return self._ctx.internal_gap_terms
    @property
    def teacher_gap_terms(self): return self._ctx.teacher_gap_terms
    @property
    def subject_spread_penalty_terms(self): return self._ctx.subject_spread_penalty_terms
    @property
    def daily_load_balance_terms(self): return self._ctx.daily_load_balance_terms


class _SolutionView:
    """Read-only view of the post-solve / result state in SolverContext."""
    __slots__ = ("_ctx",)

    def __init__(self, ctx: "SolverContext") -> None:
        object.__setattr__(self, "_ctx", ctx)

    @property
    def entries_written(self): return self._ctx.entries_written
    @property
    def entries_by_slot(self): return self._ctx.entries_by_slot
    @property
    def solver_stats(self): return self._ctx.solver_stats
    @property
    def warnings(self): return self._ctx.warnings
    @property
    def objective_score(self): return self._ctx.objective_score
    @property
    def pre_solve_metrics(self): return self._ctx.pre_solve_metrics
    @property
    def used_rooms_by_slot(self): return self._ctx.used_rooms_by_slot


def _make_subcontext_views(ctx: "SolverContext") -> None:
    """Attach sub-context view facades to *ctx* after construction.

    Call this once (e.g. at the start of _solve_program) to enable the
    structured access pattern:  ctx.data.sections,  ctx.variables.x, etc.
    This is a zero-cost operation (only object references are stored).
    """
    object.__setattr__(ctx, "_data_view", _DataView(ctx))
    object.__setattr__(ctx, "_variable_view", _VariableView(ctx))
    object.__setattr__(ctx, "_accumulator_view", _AccumulatorView(ctx))
    object.__setattr__(ctx, "_solution_view", _SolutionView(ctx))


# Attach view properties to SolverContext so they are always available
# without requiring a manual _make_subcontext_views() call.
# We use __init_subclass__ side-stepping: simply define them as cached
# properties built lazily on first access.
def _add_views() -> None:
    import functools

    def _lazy(attr: str, cls):
        @functools.cached_property
        def _prop(self):  # type: ignore[return-value]
            return cls(self)
        _prop.__set_name__(SolverContext, attr)
        setattr(SolverContext, attr, _prop)

    _lazy("data",        _DataView)
    _lazy("variables",   _VariableView)
    _lazy("accumulate",  _AccumulatorView)
    _lazy("solution",    _SolutionView)

_add_views()

