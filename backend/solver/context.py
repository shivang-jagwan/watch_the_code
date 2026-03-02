"""Shared solver context holding all data and intermediate state.

SolverContext replaces the ~50 local variables that were previously scattered
throughout the monolithic ``_solve_program`` function.  Every sub-module
(data_loader, pre_solve_locks, variables, constraints, objective,
room_assigner, result_writer) reads and writes through this single object.
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
    ):
        self.status = status
        self.entries_written = entries_written
        self.conflicts = conflicts
        self.diagnostics = diagnostics or []
        self.reason_summary = reason_summary
        self.objective_score = objective_score
        self.warnings = warnings or []
        self.solver_stats = solver_stats or {}


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

    locked_elective_sessions_by_block: dict[Any, int] = field(default_factory=lambda: defaultdict(int))
    locked_elective_sessions_by_block_day: dict[tuple[Any, int], int] = field(default_factory=lambda: defaultdict(int))
    locked_elective_block_slots: set[tuple[Any, Any]] = field(default_factory=set)
    forced_room_by_block_subject_slot: dict[tuple[Any, Any, Any], Any] = field(default_factory=dict)
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
    z: dict[tuple[Any, Any], Any] = field(default_factory=dict)
    z_by_block: dict[Any, list] = field(default_factory=lambda: defaultdict(list))
    z_by_block_day: dict[tuple[Any, int], list] = field(default_factory=lambda: defaultdict(list))

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
