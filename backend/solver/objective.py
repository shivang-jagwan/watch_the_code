"""Define the objective function for the CP-SAT model.

Multi-objective minimisation with configurable weights:
  1. Section compactness  (internal gap penalty)       — weight 500
  2. Teacher compactness  (teacher gap penalty)         — weight 300
  3. Subject day-spread   (same-subject clustering)     — weight 400
  4. Daily load balance   (max-min spread per section)  — weight 200
  5. Early-slot preference (light tiebreaker)           — weight   1
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
    W_EARLY_SLOT       =   1   # very light tiebreaker: prefer earlier starts

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

    # ── 5. Early-slot tiebreaker (very low weight) ───────────────────────
    for (_sec, _sid, slot_id), xv in ctx.x.items():
        _d, idx = ctx.slot_info.get(slot_id, (0, 0))
        obj_terms.append(xv * (idx + 1) * W_EARLY_SLOT)

    for z_key, zv in ctx.z.items():
        slot_id = None
        if isinstance(z_key, tuple):
            if len(z_key) == 2:
                _bid, slot_id = z_key
            elif len(z_key) == 3:
                _sec, _bid, slot_id = z_key
        if slot_id is None:
            continue
        _d, idx = ctx.slot_info.get(slot_id, (0, 0))
        obj_terms.append(zv * (idx + 1) * W_EARLY_SLOT)

    for (_sec, _sid, _day, start_idx), sv in ctx.lab_start.items():
        obj_terms.append(sv * (start_idx + 1) * W_EARLY_SLOT)

    # ── Minimise ─────────────────────────────────────────────────────────
    if obj_terms:
        model.Minimize(sum(obj_terms))
