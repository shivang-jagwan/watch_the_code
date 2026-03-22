from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_tenant_id, require_admin
from api.tenant import where_tenant
from core.db import get_db
from models.program import Program
from schemas.solver import ListRunEntriesResponse, ListRunsResponse, RunDetail, SolveGlobalTimetableRequest, SolveTimetableResponse
from api.routes.solver import get_run, list_run_entries, list_runs, solve_timetable_global


router = APIRouter()


class SolveAliasRequest(BaseModel):
    solver_type: Literal["GA_ONLY", "HYBRID", "CP_SAT_ONLY"]
    # Optional for strict-compat: if omitted and tenant has exactly one program,
    # that program is auto-selected.
    program_code: str | None = None
    seed: int | None = None
    max_time_seconds: float = Field(default=300.0, gt=0)

    # Optional GA/Hybrid tuning passthrough
    population_size: int | None = Field(default=None, ge=2)
    generations: int | None = Field(default=None, ge=1)
    crossover_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mutation_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    elitism_count: int | None = Field(default=None, ge=0)
    stagnation_window: int | None = Field(default=None, ge=1)
    mutation_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    target_fitness: float | None = None
    max_score: float | None = None
    tournament_k: int | None = Field(default=None, ge=2)
    cp_sat_max_time_seconds: float | None = Field(default=None, gt=0)


def _resolve_program_code(
    db: Session,
    *,
    requested_program_code: str | None,
    tenant_id: uuid.UUID | None,
) -> str:
    if requested_program_code:
        return requested_program_code

    q = where_tenant(select(Program).order_by(Program.code.asc()), Program, tenant_id)
    programs = db.execute(q).scalars().all()
    if len(programs) == 1:
        return str(programs[0].code)
    if len(programs) == 0:
        raise HTTPException(status_code=404, detail="NO_PROGRAMS_FOUND")
    raise HTTPException(status_code=422, detail="PROGRAM_CODE_REQUIRED_FOR_MULTIPLE_PROGRAMS")


@router.post("/solve", response_model=SolveTimetableResponse)
def solve_alias(
    payload: SolveAliasRequest,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    program_code = _resolve_program_code(
        db,
        requested_program_code=payload.program_code,
        tenant_id=tenant_id,
    )

    delegated = SolveGlobalTimetableRequest(
        program_code=program_code,
        solver_type=payload.solver_type,
        seed=payload.seed,
        max_time_seconds=payload.max_time_seconds,
        population_size=payload.population_size,
        generations=payload.generations,
        crossover_rate=payload.crossover_rate,
        mutation_rate=payload.mutation_rate,
        elitism_count=payload.elitism_count,
        stagnation_window=payload.stagnation_window,
        mutation_boost=payload.mutation_boost,
        target_fitness=payload.target_fitness,
        max_score=payload.max_score,
        tournament_k=payload.tournament_k,
        cp_sat_max_time_seconds=payload.cp_sat_max_time_seconds,
    )

    return solve_timetable_global(
        delegated,
        _admin=_admin,
        db=db,
        tenant_id=tenant_id,
    )


@router.get("/solve/runs", response_model=ListRunsResponse)
def list_solve_runs_alias(
    program_code: str | None = Query(default=None),
    academic_year_number: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    return list_runs(
        program_code=program_code,
        academic_year_number=academic_year_number,
        limit=limit,
        _admin=_admin,
        db=db,
        tenant_id=tenant_id,
    )


@router.get("/solve/runs/{run_id}", response_model=RunDetail)
def get_solve_run_alias(
    run_id: uuid.UUID,
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    return get_run(
        run_id=run_id,
        _admin=_admin,
        db=db,
        tenant_id=tenant_id,
    )


@router.get("/timetable", response_model=ListRunEntriesResponse)
def get_timetable_alias(
    run_id: uuid.UUID,
    section_code: str | None = Query(default=None),
    _admin=Depends(require_admin),
    db: Session = Depends(get_db),
    tenant_id: uuid.UUID | None = Depends(get_tenant_id),
):
    return list_run_entries(
        run_id=run_id,
        section_code=section_code,
        _admin=_admin,
        db=db,
        tenant_id=tenant_id,
    )
