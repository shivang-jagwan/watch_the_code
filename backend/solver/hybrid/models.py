from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from typing import Literal


Slot = tuple[int, int]


@dataclass(frozen=True)
class Institution:
    days_per_week: int
    periods_per_day: int
    lunch_break_periods: tuple[int, ...] = ()


@dataclass(frozen=True)
class Room:
    id: str
    capacity: int
    room_type: str
    available_periods: frozenset[Slot]


@dataclass(frozen=True)
class Subject:
    id: str
    requires_room_type: str
    min_lectures_per_week: int
    split_allowed: bool


@dataclass(frozen=True)
class Teacher:
    id: str
    subjects: frozenset[str]
    max_lectures_per_week: int
    availability: frozenset[Slot]
    preferences: frozenset[Slot]


@dataclass(frozen=True)
class CurriculumEntry:
    subject_id: str
    teacher_id: str
    min_per_week: int


@dataclass(frozen=True)
class ClassDef:
    id: str
    curriculum: tuple[CurriculumEntry, ...]


@dataclass(frozen=True)
class ExclusiveRoomOwnership:
    room_id: str
    subject_id: str


@dataclass(frozen=True)
class ProblemInput:
    institution: Institution
    rooms: tuple[Room, ...]
    subjects: tuple[Subject, ...]
    teachers: tuple[Teacher, ...]
    classes: tuple[ClassDef, ...]
    exclusive_room_ownership: tuple[ExclusiveRoomOwnership, ...] = ()


@dataclass(frozen=True)
class EventRequirement:
    event_id: int
    class_id: str
    subject_id: str
    teacher_id: str


@dataclass(frozen=True)
class PreparedProblem:
    problem: ProblemInput
    events: tuple[EventRequirement, ...]
    all_teaching_slots: tuple[Slot, ...]
    room_by_id: dict[str, Room]
    subject_by_id: dict[str, Subject]
    teacher_by_id: dict[str, Teacher]
    allowed_pairs_by_event: dict[int, tuple[tuple[Slot, str], ...]]
    subject_exclusive_room_ids: dict[str, frozenset[str]]
    room_exclusive_subject: dict[str, str]


@dataclass(frozen=True)
class EvolutionConfig:
    solver_type: Literal["GA_ONLY", "HYBRID"] = "HYBRID"
    population_size: int = 100
    generations: int = 500
    crossover_rate: float = 0.85
    mutation_rate: float = 0.02
    elitism_count: int = 2
    stagnation_window: int = 50
    mutation_boost: float = 0.08
    target_fitness: float = 9800.0
    max_score: float = 10000.0
    tournament_k: int = 5
    random_seed: int = 42
    cp_sat_max_time_seconds: float = 5.0


@dataclass(frozen=True)
class FitnessBreakdown:
    teacher_conflicts: int = 0
    room_conflicts: int = 0
    class_conflicts: int = 0
    exclusive_room_violations: int = 0
    missing_min_lectures: int = 0
    teacher_overload: int = 0
    uneven_distribution: int = 0
    schedule_gaps: int = 0

    def total_penalty(self) -> int:
        return (
            1000 * self.teacher_conflicts
            + 1000 * self.room_conflicts
            + 1000 * self.class_conflicts
            + 1500 * self.exclusive_room_violations
            + 500 * self.missing_min_lectures
            + 300 * self.teacher_overload
            + 100 * self.uneven_distribution
            + 50 * self.schedule_gaps
        )


@dataclass(frozen=True)
class Evaluation:
    fitness: float
    hard_ok: bool
    breakdown: FitnessBreakdown


@dataclass(frozen=True)
class EngineResult:
    best_fitness: float
    best_breakdown: FitnessBreakdown
    best_chromosome: Any
    history_best: tuple[float, ...]
    history_mean: tuple[float, ...]
    generations_ran: int
    output_paths: dict[str, str] = field(default_factory=dict)
