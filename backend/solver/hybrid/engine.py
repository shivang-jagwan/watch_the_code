from __future__ import annotations

from solver.hybrid.exporters import write_outputs
from solver.hybrid.ga_engine import evolve
from solver.hybrid.models import EngineResult, EvolutionConfig
from solver.hybrid.validation import parse_problem_input, prepare_problem


def run_hybrid_chronogen(
    data: dict,
    *,
    config: EvolutionConfig | None = None,
    output_dir: str | None = None,
) -> EngineResult:
    cfg = config or EvolutionConfig()
    problem = parse_problem_input(data)

    prepared = prepare_problem(problem)
    result = evolve(prepared, cfg)

    if output_dir is not None:
        output_paths = write_outputs(prepared, result, output_dir)
        result = EngineResult(
            best_fitness=result.best_fitness,
            best_breakdown=result.best_breakdown,
            best_chromosome=result.best_chromosome,
            history_best=result.history_best,
            history_mean=result.history_mean,
            generations_ran=result.generations_ran,
            output_paths=output_paths,
        )

    return result
