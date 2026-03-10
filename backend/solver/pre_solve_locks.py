"""Apply special allotments and fixed entries as pre-solve locks.

Extracts lines ~370-700 from the original _solve_program:
- Special allotment processing (LAB, elective block, THEORY)
- Fixed entry pre-solve locking (LAB, elective block, THEORY)
- Teacher slot pruning (weekly off day + locked slots)
- _contiguous_starts helper
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterator

from solver.context import SolverContext


def contiguous_starts(sorted_indices: list[int], block: int) -> Iterator[int]:
    """Yield start indices from *sorted_indices* where *block* contiguous slots exist."""
    if block <= 1:
        yield from sorted_indices
        return
    if not sorted_indices:
        return

    run_start = sorted_indices[0]
    prev = sorted_indices[0]
    for idx in sorted_indices[1:]:
        if idx == prev + 1:
            prev = idx
            continue

        run_end = prev
        if (run_end - run_start + 1) >= block:
            for start in range(run_start, run_end - block + 2):
                yield start

        run_start = idx
        prev = idx

    run_end = prev
    if (run_end - run_start + 1) >= block:
        for start in range(run_start, run_end - block + 2):
            yield start


def apply_pre_solve_locks(ctx: SolverContext) -> None:
    """Process special allotments and fixed entries, marking slots as locked."""
    _ensure_elective_batches(ctx)
    _warn_lunch_slot_conflicts(ctx)
    _apply_special_allotments(ctx)
    _apply_fixed_entries(ctx)
    _prune_teacher_slots(ctx)
    _filter_locked_slot_indices(ctx)


def _ensure_elective_batches(ctx: SolverContext) -> None:
    """Prepare deterministic elective section batches (default chunk size: 3)."""
    if ctx.elective_batches_by_block:
        return

    BATCH_SIZE = 3
    section_code_by_id = {getattr(s, "id", None): str(getattr(s, "code", "")) for s in ctx.sections}

    for block_id, sec_ids in ctx.sections_by_block.items():
        if not sec_ids:
            continue
        ordered = sorted(
            sec_ids,
            key=lambda sid: (section_code_by_id.get(sid, ""), str(sid)),
        )
        batches: list[list[Any]] = []
        for i in range(0, len(ordered), BATCH_SIZE):
            batch = ordered[i : i + BATCH_SIZE]
            if batch:
                batches.append(batch)
        ctx.elective_batches_by_block[block_id] = batches
        for idx, batch in enumerate(batches):
            for sid in batch:
                ctx.elective_batch_index_by_block_section[(block_id, sid)] = idx


def _warn_lunch_slot_conflicts(ctx: SolverContext) -> None:
    """Emit warnings for special allotments / fixed entries that land on a
    lunch/break slot.  These entries will be scheduled (the solver does not
    block them) but the administrator should review them."""
    if not ctx.lunch_slot_ids:
        return

    for sa in ctx.special_allotments:
        if sa.slot_id in ctx.lunch_slot_ids:
            subj = ctx.subject_by_id.get(sa.subject_id)
            sec = next((s for s in ctx.sections if s.id == sa.section_id), None)
            slot_info = ctx.slot_info.get(sa.slot_id, ("?", "?"))
            ctx.warnings.append(
                f"Special allotment for section "
                f"{getattr(sec, 'code', sa.section_id)} / "
                f"subject {getattr(subj, 'code', sa.subject_id)} "
                f"is placed on a lunch/break slot "
                f"(day {slot_info[0]}, index {slot_info[1]})."
            )

    for fe in ctx.fixed_entries:
        if fe.slot_id in ctx.lunch_slot_ids:
            subj = ctx.subject_by_id.get(fe.subject_id)
            sec = next((s for s in ctx.sections if s.id == fe.section_id), None)
            slot_info = ctx.slot_info.get(fe.slot_id, ("?", "?"))
            ctx.warnings.append(
                f"Fixed entry for section "
                f"{getattr(sec, 'code', fe.section_id)} / "
                f"subject {getattr(subj, 'code', fe.subject_id)} "
                f"is placed on a lunch/break slot "
                f"(day {slot_info[0]}, index {slot_info[1]})."
            )


def _apply_special_allotments(ctx: SolverContext) -> None:
    for sa in ctx.special_allotments:
        subj = ctx.subject_by_id.get(sa.subject_id)
        if subj is None:
            continue
        di = ctx.slot_info.get(sa.slot_id)
        if di is None:
            continue
        day, slot_idx = int(di[0]), int(di[1])

        if str(subj.subject_type) == "LAB":
            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1
            ctx.locked_lab_sessions_by_sec_subj[(sa.section_id, sa.subject_id)] += 1
            ctx.locked_lab_sessions_by_sec_subj_day[(sa.section_id, sa.subject_id, day)] += 1

            for j in range(block):
                ts = ctx.slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue

                ctx.locked_section_slots.add((sa.section_id, ts.id))
                ctx.locked_teacher_slots.add((sa.teacher_id, ts.id))
                ctx.locked_teacher_slot_day[(sa.teacher_id, ts.id)] = day
                ctx.locked_slot_indices_by_section_day[(sa.section_id, day)].add(int(slot_idx + j))

                ctx.allowed_slots_by_section[sa.section_id].discard(ts.id)
                ctx.special_room_by_section_slot[(sa.section_id, ts.id)] = sa.room_id
                ctx.special_entries_to_write.append(
                    (sa.section_id, sa.subject_id, sa.teacher_id, sa.room_id, ts.id)
                )
            continue

        # THEORY (and any other non-LAB)
        block_id = ctx.elective_block_by_section_subject.get((sa.section_id, sa.subject_id))
        if block_id is not None:
            pairs = ctx.block_subject_pairs_by_block.get(block_id, [])
            if pairs:
                batch_idx = ctx.elective_batch_index_by_block_section.get((block_id, sa.section_id))
                if batch_idx is None:
                    continue

                lock_key = (block_id, int(batch_idx), sa.slot_id)
                if lock_key not in ctx.locked_elective_block_batch_slots:
                    ctx.locked_elective_block_batch_slots.add(lock_key)
                    ctx.locked_elective_sessions_by_block_batch[(block_id, int(batch_idx))] += 1
                    ctx.locked_elective_sessions_by_block_batch_day[(block_id, int(batch_idx), day)] += 1

                    for sec_id in ctx.elective_batches_by_block.get(block_id, [])[int(batch_idx)]:
                        ctx.locked_section_slots.add((sec_id, sa.slot_id))
                        ctx.locked_slot_indices_by_section_day[(sec_id, day)].add(int(slot_idx))
                        ctx.allowed_slots_by_section[sec_id].discard(sa.slot_id)

                    for _subj_id, teacher_id in pairs:
                        ctx.locked_teacher_slots.add((teacher_id, sa.slot_id))
                        ctx.locked_teacher_slot_day[(teacher_id, sa.slot_id)] = day

                    ctx.locked_block_theory_room_demand_by_slot[sa.slot_id] += int(
                        max(0, len(pairs) - 1)
                    )

                ctx.forced_room_by_block_batch_subject_slot[(block_id, int(batch_idx), sa.subject_id, sa.slot_id)] = sa.room_id
                continue

        ctx.locked_theory_sessions_by_sec_subj[(sa.section_id, sa.subject_id)] += 1
        ctx.locked_theory_sessions_by_sec_subj_day[(sa.section_id, sa.subject_id, day)] += 1
        ctx.locked_section_slots.add((sa.section_id, sa.slot_id))
        ctx.locked_teacher_slots.add((sa.teacher_id, sa.slot_id))
        ctx.locked_teacher_slot_day[(sa.teacher_id, sa.slot_id)] = day
        ctx.locked_slot_indices_by_section_day[(sa.section_id, day)].add(int(slot_idx))

        ctx.allowed_slots_by_section[sa.section_id].discard(sa.slot_id)
        ctx.special_room_by_section_slot[(sa.section_id, sa.slot_id)] = sa.room_id
        ctx.special_entries_to_write.append(
            (sa.section_id, sa.subject_id, sa.teacher_id, sa.room_id, sa.slot_id)
        )


def _apply_fixed_entries(ctx: SolverContext) -> None:
    for fe in ctx.fixed_entries:
        subj = ctx.subject_by_id.get(fe.subject_id)
        if subj is None:
            continue
        di = ctx.slot_info.get(fe.slot_id)
        if di is None:
            continue
        day, slot_idx = int(di[0]), int(di[1])

        # Skip combined THEORY here; handled later by forcing combined_x.
        gid = ctx.combined_gid_by_sec_subj.get((fe.section_id, fe.subject_id))
        if gid is not None and str(subj.subject_type) == "THEORY":
            continue

        # Elective-block THEORY: lock the entire block occurrence.
        block_id = ctx.elective_block_by_section_subject.get((fe.section_id, fe.subject_id))
        if block_id is not None and str(subj.subject_type) == "THEORY":
            pairs = ctx.block_subject_pairs_by_block.get(block_id, [])
            if pairs:
                batch_idx = ctx.elective_batch_index_by_block_section.get((block_id, fe.section_id))
                if batch_idx is None:
                    continue

                lock_key = (block_id, int(batch_idx), fe.slot_id)
                if lock_key not in ctx.locked_elective_block_batch_slots:
                    ctx.locked_elective_block_batch_slots.add(lock_key)
                    ctx.locked_elective_sessions_by_block_batch[(block_id, int(batch_idx))] += 1
                    ctx.locked_elective_sessions_by_block_batch_day[(block_id, int(batch_idx), day)] += 1

                    for sec_id in ctx.elective_batches_by_block.get(block_id, [])[int(batch_idx)]:
                        ctx.locked_section_slots.add((sec_id, fe.slot_id))
                        ctx.locked_slot_indices_by_section_day[(sec_id, day)].add(int(slot_idx))
                        ctx.allowed_slots_by_section[sec_id].discard(fe.slot_id)

                    for _subj_id, teacher_id in pairs:
                        ctx.locked_teacher_slots.add((teacher_id, fe.slot_id))
                        ctx.locked_teacher_slot_day[(teacher_id, fe.slot_id)] = day

                    ctx.locked_block_theory_room_demand_by_slot[fe.slot_id] += int(len(pairs))

                ctx.forced_room_by_block_batch_subject_slot[(block_id, int(batch_idx), fe.subject_id, fe.slot_id)] = fe.room_id
                ctx.locked_fixed_entry_ids.add(str(fe.id))
                continue

        if str(subj.subject_type) == "LAB":
            block = int(getattr(subj, "lab_block_size_slots", 1) or 1)
            if block < 1:
                block = 1

            ctx.locked_lab_sessions_by_sec_subj[(fe.section_id, fe.subject_id)] += 1
            ctx.locked_lab_sessions_by_sec_subj_day[(fe.section_id, fe.subject_id, day)] += 1

            for j in range(block):
                ts = ctx.slot_by_day_index.get((day, slot_idx + j))
                if ts is None:
                    continue

                ctx.locked_section_slots.add((fe.section_id, ts.id))
                ctx.locked_teacher_slots.add((fe.teacher_id, ts.id))
                ctx.locked_teacher_slot_day[(fe.teacher_id, ts.id)] = day
                ctx.locked_slot_indices_by_section_day[(fe.section_id, day)].add(int(slot_idx + j))

                ctx.allowed_slots_by_section[fe.section_id].discard(ts.id)
                ctx.fixed_room_by_section_slot[(fe.section_id, ts.id)] = fe.room_id
                ctx.fixed_entries_to_write.append(
                    (fe.section_id, fe.subject_id, fe.teacher_id, fe.room_id, ts.id)
                )
            ctx.locked_fixed_entry_ids.add(str(fe.id))
            continue

        # THEORY (and any other non-LAB)
        ctx.locked_theory_sessions_by_sec_subj[(fe.section_id, fe.subject_id)] += 1
        ctx.locked_theory_sessions_by_sec_subj_day[(fe.section_id, fe.subject_id, day)] += 1
        ctx.locked_section_slots.add((fe.section_id, fe.slot_id))
        ctx.locked_teacher_slots.add((fe.teacher_id, fe.slot_id))
        ctx.locked_teacher_slot_day[(fe.teacher_id, fe.slot_id)] = day
        ctx.locked_slot_indices_by_section_day[(fe.section_id, day)].add(int(slot_idx))

        ctx.allowed_slots_by_section[fe.section_id].discard(fe.slot_id)
        ctx.fixed_room_by_section_slot[(fe.section_id, fe.slot_id)] = fe.room_id
        ctx.fixed_entries_to_write.append(
            (fe.section_id, fe.subject_id, fe.teacher_id, fe.room_id, fe.slot_id)
        )
        ctx.locked_fixed_entry_ids.add(str(fe.id))


def _prune_teacher_slots(ctx: SolverContext) -> None:
    """Build teacher_disallowed_slot_ids from locked slots, weekly off days, and time windows."""
    for teacher_id, slot_id in ctx.locked_teacher_slots:
        ctx.teacher_disallowed_slot_ids[teacher_id].add(slot_id)
    for teacher_id, teacher in ctx.teacher_by_id.items():
        if teacher.weekly_off_day is None:
            continue
        off_day = int(teacher.weekly_off_day)
        for ts in ctx.slots_by_day.get(off_day, []):
            ctx.teacher_disallowed_slot_ids[teacher_id].add(ts.id)

    # --- Teacher time-window enforcement -------------------------------------
    # For every teacher that has time windows defined, collect the set of slot
    # IDs that fall *inside* at least one window.  Any slot NOT in that set is
    # blocked.  Windows with day_of_week=None apply to every active day.
    for teacher_id, windows in ctx.teacher_windows_by_id.items():
        if not windows:
            continue

        allowed_for_teacher: set = set()
        for w in windows:
            start_si = int(w.start_slot_index)
            end_si = int(w.end_slot_index)
            if w.day_of_week is not None:
                days = [int(w.day_of_week)]
            else:
                # All active days observed by this tenant's time slots
                days = list(ctx.slots_by_day.keys())

            for day in days:
                for si in range(start_si, end_si + 1):
                    ts = ctx.slot_by_day_index.get((day, si))
                    if ts is not None:
                        allowed_for_teacher.add(ts.id)

        # Block every slot NOT inside the teacher's windows
        all_slot_ids: set = {ts.id for day_slots in ctx.slots_by_day.values() for ts in day_slots}
        for slot_id in all_slot_ids - allowed_for_teacher:
            ctx.teacher_disallowed_slot_ids[teacher_id].add(slot_id)


def check_teacher_window_feasibility(ctx: SolverContext) -> list[str]:
    """Return human-readable warnings for teacher–section pairs where the
    intersection of teacher time windows and section time windows is empty.

    Called *after* apply_pre_solve_locks() (and build_pruned_slots), so
    teacher_disallowed_slot_ids is fully populated.  Returns an empty list
    when everything is feasible.
    """
    warnings: list[str] = []
    dallowed = ctx.teacher_disallowed_slot_ids

    for section in ctx.sections:
        sec_id = section.id
        sec_allowed: set = ctx.allowed_slots_by_section.get(sec_id, set())
        if not sec_allowed:
            continue

        # Iterate over every (section, subject) → teacher assignment
        for (s_id, subj_id), teacher_id in ctx.assigned_teacher_by_section_subject.items():
            if s_id != sec_id:
                continue

            # Only flag when the teacher actually has windows configured
            if not ctx.teacher_windows_by_id.get(teacher_id):
                continue

            disallowed: set = dallowed.get(teacher_id, set())
            effective_slots = sec_allowed - disallowed
            if not effective_slots:
                teacher = ctx.teacher_by_id.get(teacher_id)
                subj = ctx.subject_by_id.get(subj_id)
                t_code = teacher.code if teacher else str(teacher_id)
                s_code = getattr(section, "code", str(sec_id))
                sub_code = subj.code if subj else str(subj_id)
                warnings.append(
                    f"Teacher {t_code} has no valid slots for section {s_code} "
                    f"subject {sub_code}: teacher availability window does not "
                    f"overlap with section timetable window."
                )

    return warnings


def _filter_locked_slot_indices(ctx: SolverContext) -> None:
    """Remove locked slot indices from per-day allowed lists."""
    for key, locked_indices in ctx.locked_slot_indices_by_section_day.items():
        if not locked_indices:
            continue
        arr = ctx.allowed_slot_indices_by_section_day.get(key)
        if not arr:
            continue
        ctx.allowed_slot_indices_by_section_day[key] = [i for i in arr if i not in locked_indices]
