"""Greedy room assignment after CP-SAT solve.

Extracts lines ~1560-1780 from the original _solve_program:
- pick_room, pick_lt_room, pick_room_for_block helpers
- Room reservation for special allotments and fixed entries
- Invariant checking helpers (_assert_entry_invariants, _sid, _rid, UUID generators)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from core.config import settings
from models.timetable_conflict import TimetableConflict
from models.timetable_entry import TimetableEntry
from solver.context import SolverContext, SolverInvariantError


def _sid(slot_id: Any) -> str:
    return str(slot_id)


def _rid(room_id: Any) -> str:
    return str(room_id)


def room_conflict_group_id(*, run_id: Any, room_id: Any, slot_id: Any) -> uuid.UUID:
    """Deterministic UUID for bypassing partial unique index on room conflicts."""
    return uuid.uuid5(uuid.NAMESPACE_OID, f"ROOM_CONFLICT:{run_id}:{room_id}:{slot_id}")


def elective_group_id(*, run_id: Any, block_id: Any, subject_id: Any, slot_id: Any) -> uuid.UUID:
    """Deterministic UUID for elective block combined entries."""
    return uuid.uuid5(
        uuid.NAMESPACE_OID, f"ELECTIVE_BLOCK:{run_id}:{block_id}:{subject_id}:{slot_id}"
    )


def assert_entry_invariants(ctx: SolverContext, entry: TimetableEntry) -> None:
    """Fail-fast check for duplicate entries before DB insert."""
    sec_id = str(entry.section_id)
    teacher_id = str(entry.teacher_id)
    room_id = str(entry.room_id)
    slot_id = str(entry.slot_id)
    combined_id = str(entry.combined_class_id) if entry.combined_class_id is not None else None

    if entry.elective_block_id is None:
        k = (sec_id, slot_id)
        if k in ctx.seen_non_elective_section_slot:
            raise SolverInvariantError(
                "SECTION_SLOT_DUPLICATE",
                "Generated duplicate non-elective section+slot entry before DB insert.",
                details={"section_id": sec_id, "slot_id": slot_id, "run_id": str(ctx.run.id)},
            )
        ctx.seen_non_elective_section_slot.add(k)

    if entry.combined_class_id is None:
        k = (room_id, slot_id)
        if k in ctx.seen_uncombined_room_slot:
            raise SolverInvariantError(
                "ROOM_SLOT_DUPLICATE",
                "Generated duplicate uncombined room+slot entry before DB insert.",
                details={"room_id": room_id, "slot_id": slot_id, "run_id": str(ctx.run.id)},
            )
        ctx.seen_uncombined_room_slot.add(k)

    tk = (teacher_id, slot_id)
    if tk not in ctx.seen_teacher_slot_event:
        ctx.seen_teacher_slot_event[tk] = combined_id
    else:
        prev = ctx.seen_teacher_slot_event[tk]
        if prev != combined_id:
            raise SolverInvariantError(
                "TEACHER_DOUBLE_BOOKING",
                "Generated teacher slot conflict before DB insert.",
                details={
                    "teacher_id": teacher_id,
                    "slot_id": slot_id,
                    "run_id": str(ctx.run.id),
                    "combined_class_id_prev": prev,
                    "combined_class_id_new": combined_id,
                },
            )


def pick_room(ctx: SolverContext, slot_id: Any, subject_type: str, section_id: Any = None, subject_id: Any = None) -> tuple[Any | None, bool]:
    """Pick a free room of the right type for *slot_id*. Returns (room_id, ok).

    If *subject_id* is provided and the subject has configured allowed rooms,
    the candidate list is restricted to those rooms only (subject-specific
    room constraint).  Otherwise the normal section-fit or global pool is used.

    OPTIMIZATION (Task 5): room candidates are pre-sorted by data_loader
    _build_room_cache() into per-(section, type) best-fit lists.  This
    eliminates the O(S) section-linear-scan and the O(R log R) per-call
    sort from the old implementation, giving O(1) lookup + O(R) scan.
    """
    sid = _sid(slot_id)

    # Subject-specific allowed rooms take priority over all other pools.
    subject_allowed = (
        ctx.allowed_rooms_by_subject.get(subject_id)
        if subject_id is not None
        else None
    )
    if subject_allowed:
        # Resolve IDs → Room objects (those still active in ctx.room_by_id).
        candidates = [ctx.room_by_id[rid] for rid in subject_allowed if rid in ctx.room_by_id]
    else:
        tag = "LAB" if subject_type == "LAB" else "THEORY"
        # Fast path: use pre-computed best-fit candidate list for this section.
        candidates = (
            ctx.room_candidates_by_section.get((section_id, tag))
            if section_id is not None
            else None
        )
        # Fallback: use the globally sorted base list (no section strength info).
        if candidates is None:
            candidates = ctx.lab_rooms_sorted if subject_type == "LAB" else ctx.theory_rooms_sorted

    if not candidates:
        return None, False

    for room in candidates:
        rid = _rid(room.id)
        if rid not in ctx.used_rooms_by_slot[sid]:
            ctx.used_rooms_by_slot[sid].add(rid)
            return room.id, True

    if getattr(settings, "solver_strict_mode", False):
        raise SolverInvariantError(
            "NO_ROOM_AVAILABLE",
            "No free room available for this slot.",
            details={"slot_id": str(slot_id), "subject_type": str(subject_type), "run_id": str(ctx.run.id)},
        )
    ctx.used_rooms_by_slot[sid].add(_rid(candidates[0].id))
    return candidates[0].id, False


def pick_lt_room(ctx: SolverContext, slot_id: Any) -> tuple[Any | None, bool]:
    """Pick a free LT (or CLASSROOM fallback) room for *slot_id*.

    OPTIMIZATION (Task 5): uses ctx.lt_plus_classroom_rooms_sorted which
    is built once by _build_room_cache() — no list construction per call.
    """
    sid = _sid(slot_id)
    candidates = ctx.lt_plus_classroom_rooms_sorted
    if not candidates:
        return None, False
    for room in candidates:
        rid = _rid(room.id)
        if rid not in ctx.used_rooms_by_slot[sid]:
            ctx.used_rooms_by_slot[sid].add(rid)
            return room.id, True
    ctx.used_rooms_by_slot[sid].add(_rid(candidates[0].id))
    if getattr(settings, "solver_strict_mode", False):
        raise SolverInvariantError(
            "NO_ROOM_AVAILABLE",
            "No free LT/CLASSROOM available for this slot.",
            details={"slot_id": str(slot_id), "room_pool": "LT+CLASSROOM", "run_id": str(ctx.run.id)},
        )
    return candidates[0].id, False


def pick_room_for_block(ctx: SolverContext, slot_ids: list[str], subject_id: Any = None) -> tuple[Any | None, bool]:
    """Pick a single LAB room free across all *slot_ids* in a block.

    If *subject_id* is provided and the subject has configured allowed rooms,
    those rooms are used instead of the full lab pool.

    OPTIMIZATION (Task 5): uses ctx.lab_rooms_sorted (pre-sorted cap ASC)
    — no list construction or sorting per call.
    """
    subject_allowed = (
        ctx.allowed_rooms_by_subject.get(subject_id)
        if subject_id is not None
        else None
    )
    if subject_allowed:
        candidates = [ctx.room_by_id[rid] for rid in subject_allowed if rid in ctx.room_by_id]
    else:
        candidates = ctx.lab_rooms_sorted
    if not candidates:
        return None, False

    for room in candidates:
        rid = _rid(room.id)
        if all(rid not in ctx.used_rooms_by_slot[_sid(sid)] for sid in slot_ids):
            for sid in slot_ids:
                ctx.used_rooms_by_slot[_sid(sid)].add(rid)
            return room.id, True

    if getattr(settings, "solver_strict_mode", False):
        raise SolverInvariantError(
            "NO_ROOM_AVAILABLE",
            "No single lab room available for the full lab block.",
            details={"slot_ids": list(slot_ids), "room_pool": "LAB", "run_id": str(ctx.run.id)},
        )
    room_id = candidates[0].id
    for sid in slot_ids:
        ctx.used_rooms_by_slot[_sid(sid)].add(_rid(room_id))
    return room_id, False


def reserve_locked_rooms(ctx: SolverContext) -> None:
    """Reserve rooms for special allotments and fixed entries, warning on conflicts."""
    run = ctx.run
    tenant_id = ctx.tenant_id

    for (sec_id, slot_id), room_id in ctx.special_room_by_section_slot.items():
        sid = _sid(slot_id)
        rid = _rid(room_id)
        if rid in ctx.used_rooms_by_slot[sid]:
            ctx.conflicting_special_room_slots.add((str(sec_id), str(slot_id)))
            ctx.db.add(
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
        ctx.used_rooms_by_slot[sid].add(rid)

    for (sec_id, slot_id), room_id in ctx.fixed_room_by_section_slot.items():
        sid = _sid(slot_id)
        rid = _rid(room_id)
        if rid in ctx.used_rooms_by_slot[sid]:
            ctx.conflicting_fixed_room_slots.add((str(sec_id), str(slot_id)))
            ctx.db.add(
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
        ctx.used_rooms_by_slot[sid].add(rid)
