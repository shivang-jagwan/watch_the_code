from __future__ import annotations

import solver.hybrid.ga_engine as ga_engine
from solver.hybrid.engine import run_hybrid_chronogen
from solver.hybrid.models import EvolutionConfig


def _sample_data() -> dict:
    return {
        "institution": {
            "days_per_week": 5,
            "periods_per_day": 7,
            "lunch_break": [3],
        },
        "rooms": [
            {"id": "R101", "capacity": 60, "type": "THEORY"},
            {"id": "R102", "capacity": 60, "type": "THEORY"},
            {"id": "R201", "capacity": 50, "type": "THEORY"},
            {"id": "LAB-AI", "capacity": 40, "type": "LAB"},
            {"id": "LAB-CS", "capacity": 40, "type": "LAB"},
        ],
        "subjects": [
            {"id": "MATH3", "requires_room_type": "THEORY", "min_lectures_per_week": 3, "split_allowed": True},
            {"id": "DSA", "requires_room_type": "THEORY", "min_lectures_per_week": 3, "split_allowed": True},
            {"id": "DBMS", "requires_room_type": "THEORY", "min_lectures_per_week": 3, "split_allowed": True},
            {"id": "AI", "requires_room_type": "THEORY", "min_lectures_per_week": 2, "split_allowed": True},
            {"id": "AI_LAB", "requires_room_type": "LAB", "min_lectures_per_week": 2, "split_allowed": False},
            {"id": "WEB", "requires_room_type": "THEORY", "min_lectures_per_week": 2, "split_allowed": True},
            {"id": "WEB_LAB", "requires_room_type": "LAB", "min_lectures_per_week": 2, "split_allowed": False},
        ],
        "teachers": [
            {"id": "T_MATH", "subjects": ["MATH3"], "max_lectures_per_week": 16},
            {"id": "T_DSA", "subjects": ["DSA"], "max_lectures_per_week": 16},
            {"id": "T_DB", "subjects": ["DBMS"], "max_lectures_per_week": 16},
            {
                "id": "T_AI",
                "subjects": ["AI", "AI_LAB"],
                "max_lectures_per_week": 16,
                "preferences": [
                    {"day": 0, "period": 0},
                    {"day": 2, "period": 2},
                    {"day": 4, "period": 5},
                ],
            },
            {"id": "T_WEB", "subjects": ["WEB", "WEB_LAB"], "max_lectures_per_week": 16},
        ],
        "classes": [
            {
                "id": "CSE-3A",
                "curriculum": [
                    {"subject_id": "MATH3", "teacher_id": "T_MATH", "min_per_week": 3},
                    {"subject_id": "DSA", "teacher_id": "T_DSA", "min_per_week": 3},
                    {"subject_id": "DBMS", "teacher_id": "T_DB", "min_per_week": 3},
                    {"subject_id": "AI", "teacher_id": "T_AI", "min_per_week": 2},
                    {"subject_id": "AI_LAB", "teacher_id": "T_AI", "min_per_week": 2},
                    {"subject_id": "WEB", "teacher_id": "T_WEB", "min_per_week": 2},
                ],
            },
            {
                "id": "CSE-3B",
                "curriculum": [
                    {"subject_id": "MATH3", "teacher_id": "T_MATH", "min_per_week": 3},
                    {"subject_id": "DSA", "teacher_id": "T_DSA", "min_per_week": 3},
                    {"subject_id": "DBMS", "teacher_id": "T_DB", "min_per_week": 3},
                    {"subject_id": "AI", "teacher_id": "T_AI", "min_per_week": 2},
                    {"subject_id": "AI_LAB", "teacher_id": "T_AI", "min_per_week": 2},
                    {"subject_id": "WEB", "teacher_id": "T_WEB", "min_per_week": 2},
                ],
            },
            {
                "id": "CSE-3C",
                "curriculum": [
                    {"subject_id": "MATH3", "teacher_id": "T_MATH", "min_per_week": 3},
                    {"subject_id": "DSA", "teacher_id": "T_DSA", "min_per_week": 3},
                    {"subject_id": "DBMS", "teacher_id": "T_DB", "min_per_week": 3},
                    {"subject_id": "AI", "teacher_id": "T_AI", "min_per_week": 2},
                    {"subject_id": "WEB", "teacher_id": "T_WEB", "min_per_week": 2},
                    {"subject_id": "WEB_LAB", "teacher_id": "T_WEB", "min_per_week": 2},
                ],
            },
        ],
        "exclusive_room_ownership": [
            {"room_id": "LAB-AI", "subject_id": "AI_LAB"},
        ],
    }


def test_ga_only_mode_does_not_call_cp_sat(monkeypatch) -> None:
    called = {"repair": 0}

    def _never_call_repair(*args, **kwargs):
        called["repair"] += 1
        raise AssertionError("CP-SAT repair must not be called in GA_ONLY mode")

    monkeypatch.setattr(ga_engine, "_repair", _never_call_repair)

    cfg = EvolutionConfig(
        solver_type="GA_ONLY",
        population_size=10,
        generations=8,
        mutation_rate=0.05,
        random_seed=42,
    )
    result = run_hybrid_chronogen(data=_sample_data(), config=cfg, output_dir=None)

    assert result.generations_ran >= 1
    assert called["repair"] == 0


def test_hybrid_mode_runs_with_cp_sat_repair() -> None:
    cfg = EvolutionConfig(
        solver_type="HYBRID",
        population_size=10,
        generations=8,
        mutation_rate=0.05,
        random_seed=42,
        cp_sat_max_time_seconds=1.0,
    )
    result = run_hybrid_chronogen(data=_sample_data(), config=cfg, output_dir=None)

    assert result.generations_ran >= 1
    b = result.best_breakdown
    assert b.teacher_conflicts == 0
    assert b.room_conflicts == 0
    assert b.class_conflicts == 0
    assert b.exclusive_room_violations == 0
