from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean

from solver.hybrid.cp_repair import RepairInfeasibleError, repair_chromosome_with_cp_sat
from solver.hybrid.encoding import Chromosome, chromosome_from_event_assignments
from solver.hybrid.fitness import evaluate_chromosome
from solver.hybrid.models import EngineResult, Evaluation, EvolutionConfig, PreparedProblem


@dataclass(frozen=True)
class PopulationEntry:
    chromosome: Chromosome
    evaluation: Evaluation


def _random_chromosome(prepared: PreparedProblem, rng: random.Random) -> Chromosome:
    assignments = {}
    for event in prepared.events:
        pairs = prepared.allowed_pairs_by_event[event.event_id]
        assignments[event.event_id] = rng.choice(pairs)
    return chromosome_from_event_assignments(prepared.events, assignments)


def _repair(prepared: PreparedProblem, chromosome: Chromosome, cfg: EvolutionConfig) -> Chromosome:
    repaired = repair_chromosome_with_cp_sat(
        prepared,
        chromosome,
        random_seed=cfg.random_seed,
        max_time_seconds=cfg.cp_sat_max_time_seconds,
    )
    return repaired.chromosome


def _evaluate(prepared: PreparedProblem, chromosome: Chromosome, cfg: EvolutionConfig) -> Evaluation:
    return evaluate_chromosome(prepared, chromosome, max_score=cfg.max_score)


def _tournament_select(population: list[PopulationEntry], rng: random.Random, k: int) -> PopulationEntry:
    candidates = rng.sample(population, k=min(k, len(population)))
    return max(candidates, key=lambda x: x.evaluation.fitness)


def _day_block_crossover(
    parent_a: Chromosome,
    parent_b: Chromosome,
    rng: random.Random,
    day_count: int,
) -> Chromosome:
    k = rng.randint(1, max(1, min(3, day_count)))
    selected_days = set(rng.sample(range(day_count), k=k))
    by_event_a = parent_a.by_event()
    by_event_b = parent_b.by_event()
    genes = []
    for event_id in sorted(by_event_a):
        ga = by_event_a[event_id]
        gb = by_event_b[event_id]
        genes.append(ga if ga.day in selected_days else gb)
    return Chromosome(genes=tuple(genes))


def _class_block_crossover(parent_a: Chromosome, parent_b: Chromosome, rng: random.Random) -> Chromosome:
    classes = sorted({g.class_id for g in parent_a.genes})
    selected_classes = set(rng.sample(classes, k=max(1, len(classes) // 2)))
    by_event_a = parent_a.by_event()
    by_event_b = parent_b.by_event()
    genes = []
    for event_id in sorted(by_event_a):
        ga = by_event_a[event_id]
        gb = by_event_b[event_id]
        genes.append(ga if ga.class_id in selected_classes else gb)
    return Chromosome(genes=tuple(genes))


def _mutate_slot_swap(chromosome: Chromosome, rng: random.Random) -> Chromosome:
    genes = list(chromosome.genes)
    if len(genes) < 2:
        return chromosome
    i, j = rng.sample(range(len(genes)), 2)
    gi = genes[i]
    gj = genes[j]
    genes[i] = type(gi)(
        event_id=gi.event_id,
        class_id=gi.class_id,
        subject_id=gi.subject_id,
        teacher_id=gi.teacher_id,
        room_id=gi.room_id,
        day=gj.day,
        period=gj.period,
    )
    genes[j] = type(gj)(
        event_id=gj.event_id,
        class_id=gj.class_id,
        subject_id=gj.subject_id,
        teacher_id=gj.teacher_id,
        room_id=gj.room_id,
        day=gi.day,
        period=gi.period,
    )
    return Chromosome(genes=tuple(genes))


def _mutate_room_reassignment(chromosome: Chromosome, prepared: PreparedProblem, rng: random.Random) -> Chromosome:
    genes = list(chromosome.genes)
    idx = rng.randrange(len(genes))
    gene = genes[idx]
    pairs = [p for p in prepared.allowed_pairs_by_event[gene.event_id] if p[0] == (gene.day, gene.period)]
    if not pairs:
        return chromosome
    new_slot, new_room = rng.choice(pairs)
    genes[idx] = type(gene)(
        event_id=gene.event_id,
        class_id=gene.class_id,
        subject_id=gene.subject_id,
        teacher_id=gene.teacher_id,
        room_id=new_room,
        day=new_slot[0],
        period=new_slot[1],
    )
    return Chromosome(genes=tuple(genes))


def _mutate_day_move(chromosome: Chromosome, prepared: PreparedProblem, rng: random.Random) -> Chromosome:
    genes = list(chromosome.genes)
    idx = rng.randrange(len(genes))
    gene = genes[idx]
    pairs = [p for p in prepared.allowed_pairs_by_event[gene.event_id] if p[0][0] != gene.day]
    if not pairs:
        return chromosome
    new_slot, new_room = rng.choice(pairs)
    genes[idx] = type(gene)(
        event_id=gene.event_id,
        class_id=gene.class_id,
        subject_id=gene.subject_id,
        teacher_id=gene.teacher_id,
        room_id=new_room,
        day=new_slot[0],
        period=new_slot[1],
    )
    return Chromosome(genes=tuple(genes))


def _mutate(chromosome: Chromosome, prepared: PreparedProblem, rng: random.Random, mutation_rate: float) -> Chromosome:
    candidate = chromosome
    if rng.random() < mutation_rate:
        candidate = _mutate_slot_swap(candidate, rng)
    if rng.random() < mutation_rate:
        candidate = _mutate_room_reassignment(candidate, prepared, rng)
    if rng.random() < mutation_rate:
        candidate = _mutate_day_move(candidate, prepared, rng)
    return candidate


def evolve(prepared: PreparedProblem, cfg: EvolutionConfig) -> EngineResult:
    rng = random.Random(cfg.random_seed)
    use_cp_repair = cfg.solver_type == "HYBRID"

    population: list[PopulationEntry] = []
    for _ in range(cfg.population_size):
        seed_chromosome = _random_chromosome(prepared, rng)
        if use_cp_repair:
            try:
                candidate = _repair(prepared, seed_chromosome, cfg)
            except RepairInfeasibleError:
                continue
        else:
            candidate = seed_chromosome
        population.append(PopulationEntry(chromosome=candidate, evaluation=_evaluate(prepared, candidate, cfg)))

    if not population:
        raise RuntimeError("Unable to create an initial feasible population")

    history_best: list[float] = []
    history_mean: list[float] = []
    best_entry = max(population, key=lambda x: x.evaluation.fitness)
    best_generation = 0

    for generation in range(cfg.generations):
        population.sort(key=lambda x: x.evaluation.fitness, reverse=True)
        current_best = population[0]
        if current_best.evaluation.fitness > best_entry.evaluation.fitness:
            best_entry = current_best
            best_generation = generation

        history_best.append(current_best.evaluation.fitness)
        history_mean.append(mean(item.evaluation.fitness for item in population))

        if current_best.evaluation.hard_ok and current_best.evaluation.fitness >= cfg.target_fitness:
            return EngineResult(
                best_fitness=current_best.evaluation.fitness,
                best_breakdown=current_best.evaluation.breakdown,
                best_chromosome=current_best.chromosome,
                history_best=tuple(history_best),
                history_mean=tuple(history_mean),
                generations_ran=generation + 1,
            )

        stagnated = (generation - best_generation) >= cfg.stagnation_window
        mutation_rate = cfg.mutation_rate + (cfg.mutation_boost if stagnated else 0.0)

        next_population: list[PopulationEntry] = []
        for elite in population[: cfg.elitism_count]:
            next_population.append(elite)

        while len(next_population) < cfg.population_size:
            parent_a = _tournament_select(population, rng, cfg.tournament_k)
            parent_b = _tournament_select(population, rng, cfg.tournament_k)

            child = parent_a.chromosome
            if rng.random() < cfg.crossover_rate:
                if rng.random() < 0.5:
                    child = _day_block_crossover(
                        parent_a.chromosome,
                        parent_b.chromosome,
                        rng,
                        prepared.problem.institution.days_per_week,
                    )
                else:
                    child = _class_block_crossover(parent_a.chromosome, parent_b.chromosome, rng)

            child = _mutate(child, prepared, rng, mutation_rate)
            if use_cp_repair:
                try:
                    repaired = _repair(prepared, child, cfg)
                except RepairInfeasibleError:
                    repaired = _repair(prepared, _random_chromosome(prepared, rng), cfg)
            else:
                repaired = child

            evaluation = _evaluate(prepared, repaired, cfg)
            next_population.append(PopulationEntry(chromosome=repaired, evaluation=evaluation))

        population = next_population

    population.sort(key=lambda x: x.evaluation.fitness, reverse=True)
    final_best = population[0]
    return EngineResult(
        best_fitness=final_best.evaluation.fitness,
        best_breakdown=final_best.evaluation.breakdown,
        best_chromosome=final_best.chromosome,
        history_best=tuple(history_best),
        history_mean=tuple(history_mean),
        generations_ran=cfg.generations,
    )
