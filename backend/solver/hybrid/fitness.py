from __future__ import annotations

from collections import Counter, defaultdict

from solver.hybrid.encoding import Chromosome
from solver.hybrid.models import Evaluation, FitnessBreakdown, PreparedProblem


def evaluate_chromosome(
    prepared: PreparedProblem,
    chromosome: Chromosome,
    *,
    max_score: float,
) -> Evaluation:
    genes = chromosome.genes

    teacher_slot_counts = Counter((g.teacher_id, g.day, g.period) for g in genes)
    class_slot_counts = Counter((g.class_id, g.day, g.period) for g in genes)
    room_slot_counts = Counter((g.room_id, g.day, g.period) for g in genes)

    teacher_conflicts = sum(v - 1 for v in teacher_slot_counts.values() if v > 1)
    class_conflicts = sum(v - 1 for v in class_slot_counts.values() if v > 1)
    room_conflicts = sum(v - 1 for v in room_slot_counts.values() if v > 1)

    exclusive_room_violations = 0
    for g in genes:
        locked_subject = prepared.room_exclusive_subject.get(g.room_id)
        if locked_subject is not None and locked_subject != g.subject_id:
            exclusive_room_violations += 1

    required_counts = Counter((e.class_id, e.subject_id) for e in prepared.events)
    actual_counts = Counter((g.class_id, g.subject_id) for g in genes)
    missing_min_lectures = 0
    for key, required in required_counts.items():
        actual = actual_counts.get(key, 0)
        if actual < required:
            missing_min_lectures += required - actual

    teacher_load = Counter(g.teacher_id for g in genes)
    teacher_overload = 0
    for teacher_id, count in teacher_load.items():
        max_lectures = prepared.teacher_by_id[teacher_id].max_lectures_per_week
        if count > max_lectures:
            teacher_overload += count - max_lectures

    per_class_subject_day = defaultdict(Counter)
    for g in genes:
        per_class_subject_day[(g.class_id, g.subject_id)][g.day] += 1
    uneven_distribution = 0
    for day_counter in per_class_subject_day.values():
        if not day_counter:
            continue
        counts = list(day_counter.values())
        uneven_distribution += max(counts) - min(counts)

    per_class_day_periods = defaultdict(list)
    for g in genes:
        per_class_day_periods[(g.class_id, g.day)].append(g.period)
    schedule_gaps = 0
    for periods in per_class_day_periods.values():
        if len(periods) <= 1:
            continue
        periods.sort()
        span = periods[-1] - periods[0] + 1
        schedule_gaps += span - len(periods)

    breakdown = FitnessBreakdown(
        teacher_conflicts=teacher_conflicts,
        room_conflicts=room_conflicts,
        class_conflicts=class_conflicts,
        exclusive_room_violations=exclusive_room_violations,
        missing_min_lectures=missing_min_lectures,
        teacher_overload=teacher_overload,
        uneven_distribution=uneven_distribution,
        schedule_gaps=schedule_gaps,
    )

    penalty = breakdown.total_penalty()
    fitness = max(0.0, float(max_score) - float(penalty))
    hard_ok = (
        teacher_conflicts == 0
        and room_conflicts == 0
        and class_conflicts == 0
        and exclusive_room_violations == 0
    )
    return Evaluation(fitness=fitness, hard_ok=hard_ok, breakdown=breakdown)
