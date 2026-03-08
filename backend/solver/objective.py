"""Define the objective function for the CP-SAT model.

Multi-objective minimisation with configurable weights:
  1. Section compactness   (internal gap penalty)       — weight 500
  2. Teacher compactness   (teacher gap penalty)         — weight 300
  3. Subject day-spread    (same-subject clustering)     — weight 400
  4. Daily load balance    (max-min spread per section)  — weight 300
  5. Late-slot preference  (earlier slots preferred)     — weight  10
  6. Friday last-slot      (avoid last slot on Fridays)  — weight  50
"""

from __future__ import annotations

from solver.context import SolverContext


def add_objective(ctx: SolverContext) -> None:
    """Add the minimisation objective to ``ctx.model``."""

    # ── Weights (relative importance) ────────────────────────────────────
    W_SECTION_GAP      = 500   # internal gaps hurt students the most
    W_SUBJECT_SPREAD   = 400   # spreading subjects across days aids learning
    W_TEACHER_GAP      = 300   # reduce wasted teacher wait-time
    W_DAILY_BALANCE    = 300   # even-out heavy vs light days
    W_LATE_SLOT        =  10   # 10 × slot_index: penalise later time slots
    W_FRIDAY_LAST      =  50   # flat penalty per class in the last slot on Friday

    # Friday is day-index 4 in the 0=Monday … 5=Saturday convention.
    FRIDAY_DAY = 4

    model = ctx.model
    obj_terms: list = []

    # ── 1. Section internal gaps ─────────────────────────────────────────
    if ctx.internal_gap_terms:
        for gv in ctx.internal_gap_terms:
            obj_terms.append(gv * W_SECTION_GAP)

    # ── 2. Subject day-spread penalty ────────────────────────────────────
    if ctx.subject_spread_penalty_terms:
        for pv in ctx.subject_spread_penalty_terms:
            obj_terms.append(pv * W_SUBJECT_SPREAD)

    # ── 3. Teacher internal gaps ─────────────────────────────────────────
    if ctx.teacher_gap_terms:
        for gv in ctx.teacher_gap_terms:
            obj_terms.append(gv * W_TEACHER_GAP)

    # ── 4. Daily load balance ────────────────────────────────────────────
    if ctx.daily_load_balance_terms:
        for sv in ctx.daily_load_balance_terms:
            obj_terms.append(sv * W_DAILY_BALANCE)

    # ── 5. Late-slot penalty (10 × slot_index — prefer earlier slots) ─────
    # While iterating, collect any variable assigned to the last slot on
    # Friday so we can apply the Friday-last-slot penalty in section 6.
    friday_day_slots = ctx.slots_by_day.get(FRIDAY_DAY, [])
    last_friday_idx: int = (
        max(int(ts.slot_index) for ts in friday_day_slots)
        if friday_day_slots
        else -1
    )
    friday_last_terms: list = []

    for (_sec, _sid, slot_id), xv in ctx.x.items():
        d, idx = ctx.slot_info.get(slot_id, (0, 0))
        obj_terms.append(xv * (idx + 1) * W_LATE_SLOT)
        if d == FRIDAY_DAY and idx == last_friday_idx:
            friday_last_terms.append(xv)

    for z_key, zv in ctx.z.items():
        slot_id = None
        if isinstance(z_key, tuple):
            if len(z_key) == 2:
                _bid, slot_id = z_key
            elif len(z_key) == 3:
                _sec, _bid, slot_id = z_key
        if slot_id is None:
            continue
        d, idx = ctx.slot_info.get(slot_id, (0, 0))
        obj_terms.append(zv * (idx + 1) * W_LATE_SLOT)
        if d == FRIDAY_DAY and idx == last_friday_idx:
            friday_last_terms.append(zv)

    for (_sec, _sid, _day, start_idx), sv in ctx.lab_start.items():
        obj_terms.append(sv * (start_idx + 1) * W_LATE_SLOT)
        if _day == FRIDAY_DAY and start_idx == last_friday_idx:
            friday_last_terms.append(sv)

    # Combined theory vars — shared across sections in a combined group;
    # previously absent from the late-slot loop.
    for (_gid, slot_id), cv in ctx.combined_x.items():
        d, idx = ctx.slot_info.get(slot_id, (0, 0))
        obj_terms.append(cv * (idx + 1) * W_LATE_SLOT)
        if d == FRIDAY_DAY and idx == last_friday_idx:
            friday_last_terms.append(cv)

    # ── 6. Friday last-slot penalty ──────────────────────────────────────
    # Penalise each class (any variable type) scheduled in the very last
    # slot on Friday.  Adds a flat W_FRIDAY_LAST penalty per assignment.
    for term in friday_last_terms:
        obj_terms.append(term * W_FRIDAY_LAST)

    # ── Minimise ─────────────────────────────────────────────────────────
    if obj_terms:
        model.Minimize(sum(obj_terms))
