"""Add hard and soft constraints to the CP-SAT model.

Extracts lines ~1040-1345 from the original _solve_program:
- Room capacity constraints (theory + lab)
- Fixed-entry hard constraints (force vars to 1)
- Section no-overlap (≤1 per slot)
- Section compactness (max gap + soft gap penalty)
- Teacher no-overlap
- Teacher weekly off day
- Teacher max continuous
- Teacher load limits (max_per_week, max_per_day)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ortools.sat.python import cp_model

from solver.context import SolverContext


def add_constraints(ctx: SolverContext) -> None:
    """Add all constraints to ``ctx.model``."""
    _add_room_capacity_constraints(ctx)
    _add_fixed_entry_hard_constraints(ctx)
    _add_section_no_overlap(ctx)
    _add_section_compactness(ctx)
    _add_subject_day_spread(ctx)
    _add_teacher_no_overlap(ctx)
    _add_teacher_weekly_off(ctx)
    _add_teacher_max_continuous(ctx)
    _add_teacher_compactness(ctx)
    _add_daily_load_balance(ctx)
    if ctx.enforce_teacher_load_limits:
        _add_teacher_load_limits(ctx)


# ── Room capacity ───────────────────────────────────────────────────────────


def _add_room_capacity_constraints(ctx: SolverContext) -> None:
    model = ctx.model
    theory_room_capacity = len(ctx.rooms_by_type.get("CLASSROOM", [])) + len(
        ctx.rooms_by_type.get("LT", [])
    )
    lab_room_capacity = len(ctx.rooms_by_type.get("LAB", []))

    # Count pre-locked special and fixed entry room demand per slot.
    for _sec_id, subj_id, _teacher_id, _room_id, slot_id in ctx.special_entries_to_write:
        room = ctx.room_by_id.get(_room_id)
        if room is not None and bool(getattr(room, "is_special", False)):
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is not None and str(subj.subject_type) == "LAB":
            ctx.special_lab_by_slot[slot_id] += 1
        else:
            ctx.special_theory_by_slot[slot_id] += 1

    for _sec_id, subj_id, _teacher_id, _room_id, slot_id in ctx.fixed_entries_to_write:
        room = ctx.room_by_id.get(_room_id)
        if room is not None and bool(getattr(room, "is_special", False)):
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is not None and str(subj.subject_type) == "LAB":
            ctx.fixed_lab_by_slot[slot_id] += 1
        else:
            ctx.fixed_theory_by_slot[slot_id] += 1

    for ts in ctx.slots:
        slot_id = ts.id
        model.Add(
            sum(ctx.room_terms_by_slot.get(slot_id, []))
            + int(ctx.special_theory_by_slot.get(slot_id, 0))
            + int(ctx.fixed_theory_by_slot.get(slot_id, 0))
            + int(ctx.locked_block_theory_room_demand_by_slot.get(slot_id, 0))
            <= int(theory_room_capacity)
        )
        model.Add(
            sum(ctx.lab_room_terms_by_slot.get(slot_id, []))
            + int(ctx.special_lab_by_slot.get(slot_id, 0))
            + int(ctx.fixed_lab_by_slot.get(slot_id, 0))
            <= int(lab_room_capacity)
        )


# ── Fixed-entry hard constraints ────────────────────────────────────────────


def _make_infeasible(model: cp_model.CpModel, _reason: str, **_kw: Any) -> None:
    model.Add(0 == 1)


def _add_fixed_entry_hard_constraints(ctx: SolverContext) -> None:
    model = ctx.model
    for fe in ctx.fixed_entries:
        if str(fe.id) in ctx.locked_fixed_entry_ids:
            continue
        subj = ctx.subject_by_id.get(fe.subject_id)
        if subj is None:
            _make_infeasible(
                model,
                "Fixed entry subject is not part of the current solve scope.",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue

        di = ctx.slot_info.get(fe.slot_id)
        if di is None:
            _make_infeasible(
                model,
                "Fixed entry references a time slot that does not exist.",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue
        day, slot_idx = int(di[0]), int(di[1])

        # Combined THEORY
        gid = ctx.combined_gid_by_sec_subj.get((fe.section_id, fe.subject_id))
        if gid is not None and str(subj.subject_type) == "THEORY":
            if getattr(fe, "teacher_id", None) is not None:
                expected_tid = ctx.group_teacher_id.get(gid)
                if expected_tid is None:
                    strict_tid = None
                    for sid in ctx.group_sections.get(gid, []):
                        _tid = ctx.assigned_teacher_by_section_subject.get((sid, fe.subject_id))
                        if _tid is None:
                            strict_tid = None
                            break
                        if strict_tid is None:
                            strict_tid = _tid
                        elif strict_tid != _tid:
                            strict_tid = None
                            break
                    expected_tid = strict_tid
                if expected_tid is not None and expected_tid != fe.teacher_id:
                    _make_infeasible(
                        model,
                        "Fixed combined-class teacher does not match the group's assigned teacher.",
                        section_id=fe.section_id,
                        subject_id=fe.subject_id,
                        teacher_id=fe.teacher_id,
                        slot_id=fe.slot_id,
                    )
                    continue

            gv = ctx.combined_x.get((gid, fe.slot_id))
            if gv is None:
                _make_infeasible(
                    model,
                    "Fixed combined-class slot is not allowed for all sections in the group.",
                    section_id=fe.section_id,
                    subject_id=fe.subject_id,
                    teacher_id=fe.teacher_id,
                    slot_id=fe.slot_id,
                )
                continue
            model.Add(gv == 1)

            for sid in ctx.group_sections.get(gid, []):
                ctx.fixed_room_by_section_slot[(sid, fe.slot_id)] = fe.room_id
            continue

        if str(subj.subject_type) == "LAB":
            sv = ctx.lab_start.get((fe.section_id, fe.subject_id, day, slot_idx))
            if sv is None:
                _make_infeasible(
                    model,
                    "Fixed lab entry must be placed on a valid lab start slot.",
                    section_id=fe.section_id,
                    subject_id=fe.subject_id,
                    teacher_id=fe.teacher_id,
                    slot_id=fe.slot_id,
                )
                continue
            model.Add(sv == 1)

            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1
            for j in range(block):
                ts = ctx.slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue
                ctx.fixed_room_by_section_slot[(fe.section_id, ts.id)] = fe.room_id
            continue

        # Regular THEORY
        xv = ctx.x.get((fe.section_id, fe.subject_id, fe.slot_id))
        if xv is None:
            _make_infeasible(
                model,
                "Fixed entry slot not allowed for the section or variable missing.",
                section_id=fe.section_id,
                subject_id=fe.subject_id,
                teacher_id=fe.teacher_id,
                slot_id=fe.slot_id,
            )
            continue
        model.Add(xv == 1)
        ctx.fixed_room_by_section_slot[(fe.section_id, fe.slot_id)] = fe.room_id


# ── Section no-overlap ──────────────────────────────────────────────────────


def _add_section_no_overlap(ctx: SolverContext) -> None:
    model = ctx.model
    for section in ctx.sections:
        for slot_id in ctx.allowed_slots_by_section[section.id]:
            terms = ctx.section_slot_terms.get((section.id, slot_id), [])
            if terms:
                model.Add(sum(terms) <= 1)


# ── Section compactness ─────────────────────────────────────────────────────


def _add_section_compactness(ctx: SolverContext) -> None:
    model = ctx.model
    MAX_EMPTY_GAP_SLOTS = 3

    for section in ctx.sections:
        sec_id = section.id
        for day in range(0, 6):
            day_slots = ctx.slots_by_day.get(day, [])
            if len(day_slots) < (MAX_EMPTY_GAP_SLOTS + 3):
                continue

            occ_list: list[tuple[int, cp_model.IntVar]] = []
            occ_vars: list[cp_model.IntVar] = []
            for ts in day_slots:
                terms = ctx.section_slot_terms.get((sec_id, ts.id), [])
                ov = model.NewBoolVar(f"occ_{sec_id}_{day}_{int(ts.slot_index)}")
                if terms:
                    model.Add(ov == sum(terms))
                else:
                    model.Add(ov == 0)
                occ_list.append((int(ts.slot_index), ov))
                occ_vars.append(ov)

            ctx.occ_by_section_day[(sec_id, day)] = occ_list

            # Hard max-gap constraint
            n = len(occ_vars)
            min_dist = MAX_EMPTY_GAP_SLOTS + 2
            for i in range(0, n):
                for j in range(i + min_dist, n):
                    middle = occ_vars[i + 1 : j]
                    if middle:
                        model.Add(occ_vars[i] + occ_vars[j] - sum(middle) <= 1)
                    else:
                        model.Add(occ_vars[i] + occ_vars[j] <= 1)

            # ── OPTIMIZATION: span-based soft gap penalty ──────────────────
            # Old approach created 3n-2 BoolVars per (section, day) using
            # prefix/suffix arrays plus per-slot gap BoolVars.
            #
            # New approach: 2 IntVars (first_occ, last_occ) + 1 IntVar
            # (gap_penalty = span - classes_count) per (section, day) that
            # has any classes.  This is O(1) aux vars instead of O(n).
            #
            #   gap_penalty = (last_occ_index - first_occ_index) - (sum(occ_vars) - 1)
            #               = number of empty slots inside the schedule window.
            # ───────────────────────────────────────────────────────────────
            classes_count = sum(occ_vars)  # LinearExpr

            # first_idx: minimum slot index that is occupied (N when none)
            # We model this as:  first_idx <= i * (1 − occ_vars[i]) + n*occ_vars[i]
            # But the cleanest Integer Programming approach uses AddMinEquality:
            #   represent each occ_vars[i] as "position if occupied else n"
            sentinel_first: list[cp_model.IntVar] = []
            sentinel_last: list[cp_model.IntVar] = []
            for i, ov in enumerate(occ_vars):
                # first_sentinel[i] = i if ov == 1 else n
                fv = model.NewIntVar(0, n, f"fs_{sec_id}_{day}_{i}")
                model.Add(fv == i).OnlyEnforceIf(ov)
                model.Add(fv == n).OnlyEnforceIf(ov.Not())
                sentinel_first.append(fv)
                # last_sentinel[i] = i if ov == 1 else -1
                lv = model.NewIntVar(-1, n - 1, f"ls2_{sec_id}_{day}_{i}")
                model.Add(lv == i).OnlyEnforceIf(ov)
                model.Add(lv == -1).OnlyEnforceIf(ov.Not())
                sentinel_last.append(lv)

            first_occ = model.NewIntVar(0, n, f"first_occ_{sec_id}_{day}")
            last_occ = model.NewIntVar(-1, n - 1, f"last_occ_{sec_id}_{day}")
            model.AddMinEquality(first_occ, sentinel_first)
            model.AddMaxEquality(last_occ, sentinel_last)

            # span = last_occ - first_occ  (0 when no classes: last=-1, first=n)
            # gap_penalty = span - (classes_count - 1)  when classes_count >= 1
            # We add the raw span as a soft penalty term (weighted in objective).
            # To avoid penalizing days with zero classes, we gate on any_class.
            any_class = model.NewBoolVar(f"any_class_{sec_id}_{day}")
            model.Add(classes_count >= 1).OnlyEnforceIf(any_class)
            model.Add(classes_count == 0).OnlyEnforceIf(any_class.Not())

            span = model.NewIntVar(0, n, f"span_{sec_id}_{day}")
            model.Add(span == last_occ - first_occ + 1).OnlyEnforceIf(any_class)
            model.Add(span == 0).OnlyEnforceIf(any_class.Not())

            gap_penalty = model.NewIntVar(0, n, f"gap_pen_{sec_id}_{day}")
            model.Add(gap_penalty == span - classes_count).OnlyEnforceIf(any_class)
            model.Add(gap_penalty == 0).OnlyEnforceIf(any_class.Not())

            ctx.internal_gap_terms.append(gap_penalty)


# ── Teacher constraints ─────────────────────────────────────────────────────


def _add_teacher_no_overlap(ctx: SolverContext) -> None:
    model = ctx.model
    for (_teacher_id, _slot_id), terms in ctx.teacher_slot_terms.items():
        if terms:
            model.Add(sum(terms) <= 1)


def _add_teacher_weekly_off(ctx: SolverContext) -> None:
    model = ctx.model
    for teacher_id, teacher in ctx.teacher_by_id.items():
        if teacher.weekly_off_day is None:
            continue
        off_day = int(teacher.weekly_off_day)
        if off_day not in ctx.teacher_active_days.get(teacher_id, set()):
            continue
        for ts in ctx.slots_by_day.get(off_day, []):
            terms = ctx.teacher_slot_terms.get((teacher_id, ts.id), [])
            if terms:
                model.Add(sum(terms) == 0)


def _add_teacher_max_continuous(ctx: SolverContext) -> None:
    model = ctx.model
    for teacher_id, teacher in ctx.teacher_by_id.items():
        max_cont = int(teacher.max_continuous)
        if max_cont <= 0:
            continue
        for day in range(0, 6):
            if day not in ctx.teacher_active_days.get(teacher_id, set()):
                continue
            day_slots = ctx.slots_by_day.get(day, [])
            if len(day_slots) <= max_cont:
                continue
            window_len = max_cont + 1
            for i in range(0, len(day_slots) - window_len + 1):
                window_slots = day_slots[i : i + window_len]
                window_terms = []
                for ts in window_slots:
                    window_terms.extend(ctx.teacher_slot_terms.get((teacher_id, ts.id), []))
                if window_terms:
                    model.Add(sum(window_terms) <= max_cont)


def _add_teacher_load_limits(ctx: SolverContext) -> None:
    model = ctx.model
    for teacher_id, teacher in ctx.teacher_by_id.items():
        all_terms = ctx.teacher_all_terms.get(teacher_id, [])
        if all_terms:
            model.Add(sum(all_terms) <= int(teacher.max_per_week))

        for day in range(0, 6):
            day_terms = ctx.teacher_day_terms.get((teacher_id, day), [])
            if day_terms:
                model.Add(sum(day_terms) <= int(teacher.max_per_day))


# ── Subject day-spread (soft) ──────────────────────────────────────────────


def _add_subject_day_spread(ctx: SolverContext) -> None:
    """Soft penalty: discourage >1 session of the same subject on the same day.

    For each (section, subject, day) where the subject already has max_per_day >= 2,
    create a penalty variable that is 1 when the section has 2+ sessions of that
    subject on the same day.  This nudges the solver to spread subjects across days
    without making it a hard constraint (which could cause infeasibility).
    """
    model = ctx.model

    # Regular theory
    for (sec_id, subj_id, day), day_x in ctx.x_by_sec_subj_day.items():
        if len(day_x) < 2:
            continue
        # If max_per_day is 1, a hard constraint already prevents doubling.
        subj = ctx.subject_by_id.get(subj_id)
        if subj is not None and int(getattr(subj, "max_per_day", 1) or 1) <= 1:
            continue
        # pv == 1 iff sum(day_x) >= 2
        # Linearisation:  2*pv <= total  AND  total <= 1 + pv*(N-1)
        #   pv=0 → total <= 1 (OK when total < 2)
        #   pv=1 → total >= 2 (forced)  AND  total <= N (always true)
        pv = model.NewBoolVar(f"spread_{sec_id}_{subj_id}_{day}")
        total = sum(day_x)
        model.Add(2 * pv <= total)                        # pv=1 → total >= 2
        model.Add(total <= 1 + pv * (len(day_x) - 1))    # total >= 2 → pv=1
        ctx.subject_spread_penalty_terms.append(pv)

    # Lab sessions (day_starts with >1 start on same day)
    for (sec_id, subj_id, day), day_starts in ctx.lab_starts_by_sec_subj_day.items():
        if len(day_starts) < 2:
            continue
        subj = ctx.subject_by_id.get(subj_id)
        if subj is not None and int(getattr(subj, "max_per_day", 1) or 1) <= 1:
            continue
        pv = model.NewBoolVar(f"spread_lab_{sec_id}_{subj_id}_{day}")
        total = sum(day_starts)
        model.Add(2 * pv <= total)
        model.Add(total <= 1 + pv * (len(day_starts) - 1))
        ctx.subject_spread_penalty_terms.append(pv)


# ── Teacher compactness (soft) ─────────────────────────────────────────────


def _add_teacher_compactness(ctx: SolverContext) -> None:
    """Soft penalty: minimise internal gaps in each teacher's daily schedule.

    Mirrors the section compactness logic but applied per-teacher.
    """
    model = ctx.model
    for teacher_id in ctx.teacher_by_id:
        for day in range(0, 6):
            if day not in ctx.teacher_active_days.get(teacher_id, set()):
                continue
            day_slots = ctx.slots_by_day.get(day, [])
            if len(day_slots) < 3:
                continue

            # Build per-slot occupancy for this teacher on this day
            occ_vars: list[cp_model.IntVar] = []
            for ts in day_slots:
                terms = ctx.teacher_slot_terms.get((teacher_id, ts.id), [])
                ov = model.NewBoolVar(f"tocc_{teacher_id}_{day}_{int(ts.slot_index)}")
                if terms:
                    model.AddMaxEquality(ov, terms)
                else:
                    model.Add(ov == 0)
                occ_vars.append(ov)

            n = len(occ_vars)
            if n < 3:
                continue

            # prefix[i] = 1 iff teacher has any class in slots [0..i]
            prefix: list[cp_model.IntVar] = []
            for i in range(n):
                pv = model.NewBoolVar(f"tpre_{teacher_id}_{day}_{i}")
                model.AddMaxEquality(pv, occ_vars[: i + 1])
                prefix.append(pv)

            # suffix[i] = 1 iff teacher has any class in slots [i..n-1]
            suffix: list[cp_model.IntVar] = []
            for i in range(n):
                sv = model.NewBoolVar(f"tsuf_{teacher_id}_{day}_{i}")
                model.AddMaxEquality(sv, occ_vars[i:])
                suffix.append(sv)

            # gap[i] = 1 iff slot i is empty but teacher has classes both before and after
            for i in range(1, n - 1):
                gv = model.NewBoolVar(f"tgap_{teacher_id}_{day}_{i}")
                model.Add(gv <= prefix[i - 1])
                model.Add(gv <= suffix[i + 1])
                model.Add(gv + occ_vars[i] <= 1)
                model.Add(gv >= prefix[i - 1] + suffix[i + 1] - occ_vars[i] - 1)
                ctx.teacher_gap_terms.append(gv)


# ── Daily load balance (soft) ──────────────────────────────────────────────


def _add_daily_load_balance(ctx: SolverContext) -> None:
    """Soft penalty: discourage putting too many classes on a single day.

    For each section, compute daily load and penalise any day that exceeds
    the 'fair share' (total_sessions / active_days).
    """
    model = ctx.model

    for section in ctx.sections:
        sec_id = section.id
        # Collect all terms per day for this section
        day_term_lists: dict[int, list] = defaultdict(list)
        for day in range(0, 6):
            for slot_id in ctx.allowed_slots_by_section.get(sec_id, set()):
                info = ctx.slot_info.get(slot_id)
                if info is None or int(info[0]) != day:
                    continue
                terms = ctx.section_slot_terms.get((sec_id, slot_id), [])
                day_term_lists[day].extend(terms)

        active_days = [d for d in range(6) if day_term_lists[d]]
        if len(active_days) < 2:
            continue

        # Create a day-load var for each active day
        day_loads: list[cp_model.IntVar] = []
        for day in active_days:
            terms = day_term_lists[day]
            if not terms:
                continue
            dv = model.NewIntVar(0, len(terms), f"dload_{sec_id}_{day}")
            model.Add(dv == sum(terms))
            day_loads.append(dv)

        if len(day_loads) < 2:
            continue

        # Penalise max - min spread; use an aux variable for the max daily load
        max_load = model.NewIntVar(0, 20, f"dmax_{sec_id}")
        min_load = model.NewIntVar(0, 20, f"dmin_{sec_id}")
        model.AddMaxEquality(max_load, day_loads)
        model.AddMinEquality(min_load, day_loads)

        spread = model.NewIntVar(0, 20, f"dspread_{sec_id}")
        model.Add(spread == max_load - min_load)
        ctx.daily_load_balance_terms.append(spread)
