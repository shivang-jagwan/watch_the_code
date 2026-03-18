from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from solver.hybrid.encoding import Chromosome, chromosome_from_event_assignments
from solver.hybrid.models import PreparedProblem


class RepairInfeasibleError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepairResult:
    chromosome: Chromosome
    objective_value: int


def repair_chromosome_with_cp_sat(
    prepared: PreparedProblem,
    chromosome: Chromosome,
    *,
    random_seed: int,
    max_time_seconds: float,
) -> RepairResult:
    model = cp_model.CpModel()

    pairs_by_event = prepared.allowed_pairs_by_event
    event_ids = [event.event_id for event in prepared.events]
    candidate_by_event = chromosome.by_event()

    pair_literals: dict[tuple[int, int], cp_model.IntVar] = {}
    pair_values_by_event: dict[int, list[tuple[tuple[int, int], str]]] = {}
    event_cost_terms: list[cp_model.IntVar] = []

    # One-hot pair assignment variables per event.
    for event_id in event_ids:
        allowed_pairs = list(pairs_by_event[event_id])
        pair_values_by_event[event_id] = allowed_pairs
        lits = []
        for pair_idx, pair in enumerate(allowed_pairs):
            lit = model.NewBoolVar(f"ev{event_id}_pair{pair_idx}")
            pair_literals[(event_id, pair_idx)] = lit
            lits.append(lit)
        model.Add(sum(lits) == 1)

        cand = candidate_by_event.get(event_id)
        cand_pair = None if cand is None else ((cand.day, cand.period), cand.room_id)
        if cand_pair is not None and cand_pair in allowed_pairs:
            same_pair_idx = allowed_pairs.index(cand_pair)
            diff_var = model.NewBoolVar(f"ev{event_id}_diff")
            model.Add(diff_var + pair_literals[(event_id, same_pair_idx)] == 1)
            event_cost_terms.append(diff_var)
        else:
            diff_var = model.NewIntVar(1, 1, f"ev{event_id}_forced_diff")
            event_cost_terms.append(diff_var)

    # H1 teacher no overlap, H2 class no overlap, H3 room no overlap.
    teacher_slot_terms: dict[tuple[str, tuple[int, int]], list[cp_model.IntVar]] = defaultdict(list)
    class_slot_terms: dict[tuple[str, tuple[int, int]], list[cp_model.IntVar]] = defaultdict(list)
    room_slot_terms: dict[tuple[str, tuple[int, int]], list[cp_model.IntVar]] = defaultdict(list)

    event_meta = {e.event_id: e for e in prepared.events}
    for event_id, allowed_pairs in pair_values_by_event.items():
        event = event_meta[event_id]
        for pair_idx, pair in enumerate(allowed_pairs):
            slot, room_id = pair
            lit = pair_literals[(event_id, pair_idx)]
            teacher_slot_terms[(event.teacher_id, slot)].append(lit)
            class_slot_terms[(event.class_id, slot)].append(lit)
            room_slot_terms[(room_id, slot)].append(lit)

    for lits in teacher_slot_terms.values():
        model.Add(sum(lits) <= 1)
    for lits in class_slot_terms.values():
        model.Add(sum(lits) <= 1)
    for lits in room_slot_terms.values():
        model.Add(sum(lits) <= 1)

    # H6 exclusive room ownership is guaranteed by prefiltered domains,
    # but we enforce it explicitly for safety.
    room_exclusive_subject = prepared.room_exclusive_subject
    for event_id, allowed_pairs in pair_values_by_event.items():
        event = event_meta[event_id]
        for pair_idx, (_, room_id) in enumerate(allowed_pairs):
            locked_subject = room_exclusive_subject.get(room_id)
            if locked_subject is not None and locked_subject != event.subject_id:
                model.Add(pair_literals[(event_id, pair_idx)] == 0)

    model.Minimize(sum(event_cost_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time_seconds)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = int(random_seed)

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RepairInfeasibleError("CP-SAT repair failed to find a feasible timetable")

    repaired_assignments: dict[int, tuple[tuple[int, int], str]] = {}
    for event_id, allowed_pairs in pair_values_by_event.items():
        selected = None
        for pair_idx, pair in enumerate(allowed_pairs):
            lit = pair_literals[(event_id, pair_idx)]
            if solver.Value(lit) == 1:
                selected = pair
                break
        if selected is None:
            raise RepairInfeasibleError(f"No selected pair for event {event_id}")
        repaired_assignments[event_id] = selected

    repaired = chromosome_from_event_assignments(prepared.events, repaired_assignments)
    return RepairResult(chromosome=repaired, objective_value=int(solver.ObjectiveValue()))
