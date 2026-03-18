# ChronoGen Hybrid Timetable Engine

This module implements a production-oriented hybrid architecture:

- Genetic Algorithm (GA) for exploration
- OR-Tools CP-SAT for hard-constraint repair and enforcement

The implementation is deterministic with fixed random seed and produces exportable timetable artifacts.

## Features

- Strict input model for institution, rooms, subjects, teachers, classes
- Pre-validation before optimization starts
- Event-based chromosome representation
- CP-SAT hard-constraint repair layer per chromosome
- Penalty-based fitness scoring for soft constraints
- Tournament selection, two crossover strategies, three mutation operators
- Elitism + adaptive mutation boost on stagnation
- JSON, CSV, HTML timetable exports + convergence SVG graph
- Exclusive room ownership support (hard enforced + heavy penalty)

## Hard Constraints Enforced by CP-SAT

- H1 teacher no double-booking
- H2 class no double-booking
- H3 room no double-booking
- H4 subject-room type compatibility
- H5 teacher availability
- H6 exclusive room ownership

## Fitness Function

Fitness uses penalties exactly as required:

- `1000 * teacher_conflicts`
- `1000 * room_conflicts`
- `1000 * class_conflicts`
- `1500 * exclusive_room_violations`
- `500 * missing_min_lectures`
- `300 * teacher_overload`
- `100 * uneven_distribution`
- `50 * schedule_gaps`

`fitness = max_score - total_penalty`

## Config

`EvolutionConfig` defaults:

- population_size: 100
- generations: 500
- crossover_rate: 0.85
- mutation_rate: 0.02
- elitism_count: 2
- stagnation_window: 50
- mutation_boost: 0.08
- target_fitness: 9800
- random_seed: 42

## Usage

Run mode tests:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_hybrid_modes.py -q
```

## API Integration

Hybrid behavior is integrated into the main DB-driven solve flow with solver mode selection:

- `GA_ONLY` for pure genetic search
- `HYBRID` for genetic search with CP-SAT repair

Use the primary solve endpoints and set `solver_type` accordingly.

## Output Artifacts

Generated under `backend/outputs/chronogen_hybrid_demo/`:

- `timetable_full.json`
- `timetable_lectures.csv`
- `timetable.html`
- `fitness_convergence.svg`

## Extension Points

- Replace JSON ingestion with API payload or CSV parser
- Add weighted teacher preference objective terms in CP-SAT repair
- Add optional ICS/PDF exporters
- Integrate with existing FastAPI solver endpoints
