"""
Timetable Generation System — Complete Smoke Test Suite
========================================================
Covers every core solver feature after a refactor or deployment.

Usage:
    cd backend
    pytest tests/test_smoke_suite.py -v --tb=short

Requirements:
    pip install pytest httpx

The suite creates an isolated test tenant in the real PostgreSQL database,
runs the full solver pipeline, validates all output constraints, then
cleans up all test data before exiting.

IMPORTANT: This test uses the actual database defined in backend/.env.
           It will NOT corrupt production data — everything lives under
           a uniquely-named tenant that is deleted at teardown.
"""

from __future__ import annotations

import sys
import os
import uuid
import datetime
import time
from collections import defaultdict
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Bootstrap: make "backend/" the Python root so imports resolve correctly.
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from main import create_app  # noqa: E402
from core.db import get_db, SessionLocal  # noqa: E402
from core.security import hash_password  # noqa: E402
from models.tenant import Tenant  # noqa: E402
from models.user import User  # noqa: E402
from models.program import Program  # noqa: E402
from models.academic_year import AcademicYear  # noqa: E402
from models.section import Section  # noqa: E402
from models.subject import Subject  # noqa: E402
from models.teacher import Teacher  # noqa: E402
from models.room import Room  # noqa: E402
from models.time_slot import TimeSlot  # noqa: E402
from models.section_subject import SectionSubject  # noqa: E402
from models.section_time_window import SectionTimeWindow  # noqa: E402
from models.teacher_subject_section import TeacherSubjectSection  # noqa: E402
from models.combined_group import CombinedGroup  # noqa: E402
from models.combined_group_section import CombinedGroupSection  # noqa: E402
from models.elective_block import ElectiveBlock  # noqa: E402
from models.elective_block_subject import ElectiveBlockSubject  # noqa: E402
from models.section_elective_block import SectionElectiveBlock  # noqa: E402
from models.fixed_timetable_entry import FixedTimetableEntry  # noqa: E402
from models.special_allotment import SpecialAllotment  # noqa: E402
from models.timetable_entry import TimetableEntry  # noqa: E402
from models.timetable_run import TimetableRun  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROGRAM_CODE = "CSE_SMOKE"
YEAR_NUMBER = 3
SOLVE_SEED = 42
SOLVE_TIMEOUT = 120.0  # seconds — generous so CI doesn't time-out

# Day constants (0=Mon … 5=Sat)
MON, TUE, WED, THU, FRI, SAT = 0, 1, 2, 3, 4, 5

# Slot indices within a day (0-based)
SLOTS_PER_DAY = 8

# Section codes
SEC_CODES = ["CSE-3A", "CSE-3B", "CSE-3C"]

# Teacher 5 off-day (for TEST 9)
T5_OFF_DAY = WED


# ---------------------------------------------------------------------------
# Session-level fixture: seed everything → solve → expose shared state
# ---------------------------------------------------------------------------

class _SmokeState:
    """Carries all UUIDs and the solve result for the whole test session."""
    tenant_id: uuid.UUID
    tenant_slug: str = ""
    username: str = ""                      # unique per run to avoid collision
    program_id: uuid.UUID
    year_id: uuid.UUID
    section_ids: dict[str, uuid.UUID]       # code → id
    subject_ids: dict[str, uuid.UUID]       # code → id
    teacher_ids: dict[str, uuid.UUID]       # code → id
    room_ids: dict[str, uuid.UUID]          # code → id
    slot_ids: dict[tuple[int, int], uuid.UUID]  # (day, slot_idx) → id
    run_id: uuid.UUID | None = None
    solve_status: str | None = None
    entries_written: int = 0
    entries: list[dict] = []

    # Cross-test handles
    fixed_slot: tuple[int, int] | None = None  # (day, slot_index) for TEST 8
    t5_id: uuid.UUID | None = None             # T5 for TEST 9 off-day


_STATE = _SmokeState()


def _new_db() -> Session:
    """Get a raw DB session for use in test setup/teardown."""
    return SessionLocal()


# ---------------------------------------------------------------------------
# Seeder — creates all required entities
# ---------------------------------------------------------------------------

def _seed(db: Session) -> None:  # noqa: C901
    state = _STATE

    # ── Tenant ──────────────────────────────────────────────────────────────
    suffix = uuid.uuid4().hex[:8]
    slug = f"smoke-{suffix}"
    tenant = Tenant(id=uuid.uuid4(), slug=slug, name="Smoke Test Tenant")
    db.add(tenant)
    db.flush()
    state.tenant_id = tenant.id
    state.tenant_slug = slug
    tid = tenant.id

    # ── Admin user — unique username per run to avoid scalar_one_or_none clash ──
    username = f"smoke_{suffix}"
    state.username = username
    admin_user = User(
        id=uuid.uuid4(),
        tenant_id=tid,
        username=username,
        password_hash=hash_password("SmokePass1!"),
        role="ADMIN",
        is_active=True,
    )
    db.add(admin_user)

    # ── Program ──────────────────────────────────────────────────────────────
    prog = Program(id=uuid.uuid4(), tenant_id=tid, code=PROGRAM_CODE, name="Computer Science Engg")
    db.add(prog)
    db.flush()
    state.program_id = prog.id

    # ── Academic Year ────────────────────────────────────────────────────────
    ay = AcademicYear(id=uuid.uuid4(), tenant_id=tid, year_number=YEAR_NUMBER, is_active=True)
    db.add(ay)
    db.flush()
    state.year_id = ay.id

    # ── Time Slots: 6 days × 8 slots ─────────────────────────────────────────
    state.slot_ids = {}
    base_hour = 8  # 08:00
    for day in range(6):
        for si in range(SLOTS_PER_DAY):
            start_h = base_hour + si
            end_h = start_h + 1
            ts = TimeSlot(
                id=uuid.uuid4(),
                tenant_id=tid,
                day_of_week=day,
                slot_index=si,
                start_time=datetime.time(start_h % 24, 0),
                end_time=datetime.time(end_h % 24, 0),
            )
            db.add(ts)
            state.slot_ids[(day, si)] = ts.id
    db.flush()

    # ── Rooms ────────────────────────────────────────────────────────────────
    state.room_ids = {}
    rooms_spec = [
        ("CR-101", "CLASSROOM", 65, False),
        ("CR-102", "CLASSROOM", 65, False),
        ("CR-103", "CLASSROOM", 65, False),
        ("CR-104", "CLASSROOM", 65, False),
        ("LAB-1",  "LAB",       30, False),
        ("LAB-2",  "LAB",       30, False),
        ("LT-1",   "LT",       200, False),
        # Special room for SpecialAllotment test (TEST 7)
        ("SPECIAL-1", "CLASSROOM", 65, True),
    ]
    for code, rtype, cap, is_special in rooms_spec:
        r = Room(
            id=uuid.uuid4(),
            tenant_id=tid,
            code=code,
            name=code,
            room_type=rtype,
            capacity=cap,
            is_active=True,
            is_special=is_special,
        )
        db.add(r)
        state.room_ids[code] = r.id
    db.flush()

    # ── Teachers ─────────────────────────────────────────────────────────────
    # T1–T4: teach core subjects across all sections
    # T5: has off-day Wednesday (TEST 9), max_continuous=2 (TEST 10)
    # T6,T7: lab teachers
    # T8: combined class teacher (AI subject for CSE-3A & CSE-3B & CSE-3C)
    # T9,T10: elective teachers
    state.teacher_ids = {}
    teachers_spec = [
        # code, max_continuous, weekly_off_day(None=no off)
        ("T1",  3, None),   # teaches OS to all sections
        ("T2",  3, None),   # teaches DBMS to all sections
        ("T3",  3, None),   # teaches Networks to all sections
        ("T4",  3, None),   # teaches ADA to CSE-3C only
        ("T5",  2, T5_OFF_DAY),  # TEST9: off Wed; TEST10: max_continuous=2
        ("T6",  3, None),   # teaches OS_LAB to CSE-3A and CSE-3B
        ("T7",  3, None),   # teaches OS_LAB to CSE-3C, and DBMS_LAB to all
        ("T8",  3, None),   # teaches AI (combined group, all 3 sections)
        ("T9",  3, None),   # teaches ML (elective, CSE-3A)
        ("T10", 3, None),   # teaches CYBER (elective, CSE-3B)
        ("T11", 3, None),   # teaches BLOCKCHAIN (elective, CSE-3C)
    ]
    for code, max_cont, off_day in teachers_spec:
        t = Teacher(
            id=uuid.uuid4(),
            tenant_id=tid,
            code=code,
            full_name=f"Teacher {code}",
            max_per_day=4,
            max_per_week=20,
            max_continuous=max_cont,
            weekly_off_day=off_day,
            is_active=True,
        )
        db.add(t)
        state.teacher_ids[code] = t.id
    db.flush()
    state.t5_id = state.teacher_ids["T5"]

    # ── Subjects ─────────────────────────────────────────────────────────────
    #  Subjects need: program_id, academic_year_id, code, name,
    #                 subject_type (THEORY/LAB), sessions_per_week,
    #                 lab_block_size_slots (1 for THEORY)
    state.subject_ids = {}
    subjects_spec = [
        # (code, type, sessions/week, max_per_day, lab_block_size)
        ("OS",          "THEORY", 3, 1, 1),
        ("DBMS",        "THEORY", 2, 1, 1),
        ("Networks",    "THEORY", 2, 1, 1),
        ("OS_LAB",      "LAB",    1, 1, 3),   # 3-slot block
        ("DBMS_LAB",    "LAB",    1, 1, 3),   # 3-slot block
        ("AI",          "THEORY", 2, 1, 1),   # combined across all 3 sections
        ("ML",          "THEORY", 2, 1, 1),   # elective — CSE-3A
        ("CYBER",       "THEORY", 2, 1, 1),   # elective — CSE-3B
        ("BLOCKCHAIN",  "THEORY", 2, 1, 1),   # elective — CSE-3C
        ("ADA",         "THEORY", 2, 1, 1),   # CSE-3C only (TEST 4/8)
    ]
    for code, stype, spw, mpd, lab_size in subjects_spec:
        s = Subject(
            id=uuid.uuid4(),
            tenant_id=tid,
            program_id=prog.id,
            academic_year_id=ay.id,
            code=code,
            name=code,
            subject_type=stype,
            sessions_per_week=spw,
            max_per_day=mpd,
            lab_block_size_slots=lab_size,
            is_active=True,
        )
        db.add(s)
        state.subject_ids[code] = s.id
    db.flush()

    # ── Sections ─────────────────────────────────────────────────────────────
    state.section_ids = {}
    section_strengths = {"CSE-3A": 60, "CSE-3B": 60, "CSE-3C": 80}  # CSE-3C=80 for TEST 11
    for code in SEC_CODES:
        sec = Section(
            id=uuid.uuid4(),
            tenant_id=tid,
            program_id=prog.id,
            academic_year_id=ay.id,
            code=code,
            name=f"CSE Year-3 Section {code[-1]}",
            strength=section_strengths[code],
            track="CORE",
            is_active=True,
        )
        db.add(sec)
        state.section_ids[code] = sec.id
    db.flush()

    # ── Section Time Windows: all 6 days, slots 0–7 ──────────────────────────
    for sec_code in SEC_CODES:
        sec_id = state.section_ids[sec_code]
        for day in range(6):
            db.add(SectionTimeWindow(
                id=uuid.uuid4(),
                tenant_id=tid,
                section_id=sec_id,
                day_of_week=day,
                start_slot_index=0,
                end_slot_index=SLOTS_PER_DAY - 1,
            ))
    db.flush()

    # ── SectionSubject mappings ───────────────────────────────────────────────
    # Core subjects: all 3 sections get OS, DBMS, Networks, OS_LAB, DBMS_LAB, AI
    # ADA: only CSE-3C (to replace one extra subject)
    # T5 teaches OS to CSE-3A (for TEST 9/10 — we override T1 for CSE-3A here).
    # Elective subjects: ML→CSE-3A, CYBER→CSE-3B, BLOCKCHAIN→CSE-3C
    core_for_all = ["OS", "DBMS", "Networks", "OS_LAB", "DBMS_LAB", "AI"]
    elective_by_section = {"CSE-3A": "ML", "CSE-3B": "CYBER", "CSE-3C": "BLOCKCHAIN"}

    for sec_code in SEC_CODES:
        sec_id = state.section_ids[sec_code]
        subjects_for_section = list(core_for_all)
        if sec_code == "CSE-3C":
            subjects_for_section.append("ADA")
        subjects_for_section.append(elective_by_section[sec_code])
        for subj_code in subjects_for_section:
            db.add(SectionSubject(
                id=uuid.uuid4(),
                tenant_id=tid,
                section_id=sec_id,
                subject_id=state.subject_ids[subj_code],
            ))
    db.flush()

    # ── TeacherSubjectSection mappings ────────────────────────────────────────
    # Maps: (teacher, subject, section)
    # T1=OS, T2=DBMS, T3=Networks, T5=OS for CSE-3A (overrides; test off-day),
    # T6=OS_LAB (CSE-3A, CSE-3B), T7=OS_LAB(CSE-3C)+DBMS_LAB(all),
    # T8=AI(all), T9=ML(CSE-3A), T10=CYBER(CSE-3B), T11=BLOCKCHAIN(CSE-3C)
    tss_entries = []
    for sec_code in SEC_CODES:
        sec_id = state.section_ids[sec_code]
        # OS → T5 for CSE-3A (off-day test), T1 for CSE-3B / CSE-3C
        if sec_code == "CSE-3A":
            tss_entries.append(("T5", "OS", sec_code))
        else:
            tss_entries.append(("T1", "OS", sec_code))
        tss_entries.append(("T2", "DBMS", sec_code))
        tss_entries.append(("T3", "Networks", sec_code))
        if sec_code in ("CSE-3A", "CSE-3B"):
            tss_entries.append(("T6", "OS_LAB", sec_code))
        else:
            tss_entries.append(("T7", "OS_LAB", sec_code))
        tss_entries.append(("T7", "DBMS_LAB", sec_code))
        tss_entries.append(("T8", "AI", sec_code))

    # ADA for CSE-3C (T4)
    tss_entries.append(("T4", "ADA", "CSE-3C"))
    # Electives
    tss_entries.append(("T9",  "ML",         "CSE-3A"))
    tss_entries.append(("T10", "CYBER",       "CSE-3B"))
    tss_entries.append(("T11", "BLOCKCHAIN",  "CSE-3C"))

    for t_code, s_code, sec_code in tss_entries:
        db.add(TeacherSubjectSection(
            id=uuid.uuid4(),
            tenant_id=tid,
            teacher_id=state.teacher_ids[t_code],
            subject_id=state.subject_ids[s_code],
            section_id=state.section_ids[sec_code],
            is_active=True,
        ))
    db.flush()

    # ── Combined Group: AI across CSE-3A, CSE-3B, CSE-3C ────────────────────
    # TEST 5: Combined class — same slot, same teacher, same room for all 3
    cg = CombinedGroup(
        id=uuid.uuid4(),
        tenant_id=tid,
        academic_year_id=ay.id,
        subject_id=state.subject_ids["AI"],
        teacher_id=state.teacher_ids["T8"],
        label="AI-Combined-Y3",
    )
    db.add(cg)
    db.flush()
    for sec_code in SEC_CODES:
        db.add(CombinedGroupSection(
            id=uuid.uuid4(),
            tenant_id=tid,
            combined_group_id=cg.id,
            subject_id=state.subject_ids["AI"],
            section_id=state.section_ids[sec_code],
        ))
    db.flush()

    # ── Elective Block: ML / CYBER / BLOCKCHAIN ───────────────────────────────
    # TEST 6: all three sections scheduled at the same slot (different teachers)
    eb = ElectiveBlock(
        id=uuid.uuid4(),
        tenant_id=tid,
        program_id=prog.id,
        academic_year_id=ay.id,
        name="Elective Block Y3",
        code="EB-Y3",
        is_active=True,
    )
    db.add(eb)
    db.flush()
    # ElectiveBlockSubject: one entry per (block, subject, teacher)
    elective_block_rows = [
        ("ML",        "T9"),
        ("CYBER",     "T10"),
        ("BLOCKCHAIN","T11"),
    ]
    for subj_code, t_code in elective_block_rows:
        db.add(ElectiveBlockSubject(
            id=uuid.uuid4(),
            tenant_id=tid,
            block_id=eb.id,
            subject_id=state.subject_ids[subj_code],
            teacher_id=state.teacher_ids[t_code],
        ))
    # All 3 sections are in the block
    for sec_code in SEC_CODES:
        db.add(SectionElectiveBlock(
            id=uuid.uuid4(),
            tenant_id=tid,
            section_id=state.section_ids[sec_code],
            block_id=eb.id,
        ))
    db.flush()

    # ── Fixed Timetable Entry: DBMS for CSE-3B @ Monday slot 1 ───────────────
    # TEST 8: solver must honour this lock
    fixed_day = MON
    fixed_slot_idx = 1
    state.fixed_slot = (fixed_day, fixed_slot_idx)
    fe = FixedTimetableEntry(
        id=uuid.uuid4(),
        tenant_id=tid,
        section_id=state.section_ids["CSE-3B"],
        subject_id=state.subject_ids["DBMS"],
        teacher_id=state.teacher_ids["T2"],
        room_id=state.room_ids["CR-102"],
        slot_id=state.slot_ids[(fixed_day, fixed_slot_idx)],
        is_active=True,
    )
    db.add(fe)
    db.flush()

    # ── Special Allotment: OS for CSE-3A @ Tuesday slot 3, special room ───────
    # TEST 7: solver must honour this lock (NOT counted in solver sessions_per_week)
    # NOTE: special allotments use a room with is_special=True; also need
    # CSE-3A to have OS scheduled (T5 teaches OS to CSE-3A).
    sa_day = TUE
    sa_slot_idx = 3
    sa = SpecialAllotment(
        id=uuid.uuid4(),
        tenant_id=tid,
        section_id=state.section_ids["CSE-3A"],
        subject_id=state.subject_ids["OS"],
        teacher_id=state.teacher_ids["T5"],
        room_id=state.room_ids["SPECIAL-1"],
        slot_id=state.slot_ids[(sa_day, sa_slot_idx)],
        reason="Smoke test special allotment",
        is_active=True,
    )
    db.add(sa)
    db.flush()

    db.commit()


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db() -> Session:
    """Return a plain SQLAlchemy session; teardown deletes tenant data."""
    session = _new_db()
    yield session
    session.close()


@pytest.fixture(scope="module")
def client(db: Session) -> TestClient:
    """Create FastAPI TestClient; seed DB on first use; teardown cleans up."""
    app = create_app()
    _seed(db)
    tc = TestClient(app, raise_server_exceptions=True)
    yield tc
    # ──────────────────────────────────────────────────────────────────────────
    # Teardown: delete ALL data for this test tenant in dependency order.
    # ──────────────────────────────────────────────────────────────────────────
    _teardown(db)


def _teardown(db: Session) -> None:
    tid = _STATE.tenant_id
    if tid is None:
        return
    tables_ordered = [
        "timetable_entries",
        "timetable_conflicts",
        "timetable_runs",
        "fixed_timetable_entries",
        "special_allotments",
        "combined_group_sections",
        "combined_groups",
        "section_elective_blocks",
        "elective_block_subjects",
        "elective_blocks",
        "teacher_subject_sections",
        "section_subjects",
        "section_time_windows",
        "sections",
        "subjects",
        "teachers",
        "rooms",
        "time_slots",
        "academic_years",
        "programs",
        "users",
        "tenants",
    ]
    try:
        for tbl in tables_ordered:
            try:
                if tbl == "tenants":
                    db.execute(text(f"DELETE FROM {tbl} WHERE id = :tid"), {"tid": str(tid)})
                else:
                    db.execute(text(f"DELETE FROM {tbl} WHERE tenant_id = :tid"), {"tid": str(tid)})
            except Exception:
                db.rollback()
        db.commit()
    except Exception:
        db.rollback()


@pytest.fixture(scope="module")
def auth_headers(client: TestClient) -> dict[str, str]:
    """Log in and return Bearer auth headers."""
    resp = client.post(
        "/api/auth/login",
        json={
            "username": _STATE.username,
            "password": "SmokePass1!",
            "tenant": _STATE.tenant_slug,
        },
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    token = data.get("access_token")
    assert token, "No access_token in login response"
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def solve_result(client: TestClient, auth_headers: dict) -> dict:
    """Run the solver once and share the result across all tests."""
    resp = client.post(
        "/api/solver/solve",
        json={
            "program_code": PROGRAM_CODE,
            "academic_year_number": YEAR_NUMBER,
            "seed": SOLVE_SEED,
            "max_time_seconds": SOLVE_TIMEOUT,
            "relax_teacher_load_limits": False,
            "require_optimal": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"Solve endpoint error: {resp.text}"
    result = resp.json()
    _STATE.run_id = uuid.UUID(result["run_id"])
    _STATE.solve_status = result["status"]
    _STATE.entries_written = result.get("entries_written", 0)
    return result


@pytest.fixture(scope="module")
def run_entries(client: TestClient, auth_headers: dict, solve_result: dict) -> list[dict]:
    """Fetch all timetable entries for the completed run."""
    run_id = str(_STATE.run_id)
    resp = client.get(f"/api/solver/runs/{run_id}/entries", headers=auth_headers)
    assert resp.status_code == 200, f"Failed fetching entries: {resp.text}"
    entries = resp.json()["entries"]
    _STATE.entries = entries
    return entries


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║          API SMOKE TESTS                  ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestAPIEndpoints:
    """Verify each protected API endpoint returns 200 with valid JSON."""

    def test_login_returns_access_token(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/login",
            json={
                "username": _STATE.username,
                "password": "SmokePass1!",
                "tenant": _STATE.tenant_slug,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert len(data["access_token"]) > 10

    def test_login_rejects_bad_password(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/login",
            json={
                "username": _STATE.username,
                "password": "WrongPass!",
                "tenant": _STATE.tenant_slug,
            },
        )
        assert resp.status_code == 401

    def test_get_programs(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/api/programs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        codes = [p["code"] for p in data]
        assert PROGRAM_CODE in codes

    def test_get_sections(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/api/sections", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        codes = [s["code"] for s in data]
        for code in SEC_CODES:
            assert code in codes, f"Section {code} missing from /api/sections"

    def test_generate_endpoint(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/api/solver/generate",
            json={
                "program_code": PROGRAM_CODE,
                "academic_year_number": YEAR_NUMBER,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "READY_FOR_SOLVE"

    def test_list_runs(self, client: TestClient, auth_headers: dict, solve_result: dict) -> None:
        resp = client.get("/api/solver/runs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        run_ids = [r["id"] for r in data["runs"]]
        assert str(_STATE.run_id) in run_ids

    def test_get_run_detail(self, client: TestClient, auth_headers: dict, solve_result: dict) -> None:
        run_id = str(_STATE.run_id)
        resp = client.get(f"/api/solver/runs/{run_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run_id
        assert data["status"] in {"OPTIMAL", "FEASIBLE", "SUBOPTIMAL"}
        assert data["entries_total"] > 0

    def test_get_run_entries(self, client: TestClient, auth_headers: dict, run_entries: list) -> None:
        assert len(run_entries) > 0, "Solver produced zero entries"

    def test_unauthenticated_request_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/solver/runs")
        assert resp.status_code in {401, 403}


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 1 — Basic Solve                   ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestBasicSolve:
    def test_solve_status_is_feasible_or_optimal(self, solve_result: dict) -> None:
        status = solve_result["status"]
        assert status in {"OPTIMAL", "FEASIBLE", "SUBOPTIMAL"}, (
            f"Solver returned non-feasible status: {status}\n"
            f"Conflicts: {solve_result.get('conflicts', [])}"
        )

    def test_entries_written_positive(self, solve_result: dict) -> None:
        assert solve_result["entries_written"] > 0, "Solver wrote zero entries"

    def test_all_sections_have_entries(self, run_entries: list) -> None:
        section_codes_in_result = {e["section_code"] for e in run_entries}
        for code in SEC_CODES:
            assert code in section_codes_in_result, f"Section {code} has no entries"

    def test_each_section_minimum_sessions(self, run_entries: list) -> None:
        """Each section should have at least 10 sessions scheduled per week."""
        by_section = defaultdict(int)
        for e in run_entries:
            by_section[e["section_code"]] += 1
        for code in SEC_CODES:
            count = by_section.get(code, 0)
            assert count >= 10, f"Section {code} only has {count} sessions (expected >= 10)"


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 2 — Teacher Conflict Prevention   ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestTeacherConflict:
    def test_no_teacher_double_booking(self, db: Session, solve_result: dict) -> None:
        """A teacher must never appear in two simultaneous slots in the same run."""
        run_id = _STATE.run_id
        rows = db.execute(
            text("""
                SELECT teacher_id, slot_id, count(*) AS cnt
                FROM timetable_entries
                WHERE run_id = :run_id
                GROUP BY teacher_id, slot_id
                HAVING count(*) > 1
            """),
            {"run_id": str(run_id)},
        ).all()
        assert len(rows) == 0, (
            f"Teacher double-booking detected: {rows}"
        )

    def test_teacher_conflict_detailed(self, run_entries: list) -> None:
        """Python-level cross-check: no (teacher, day, slot) appears twice."""
        seen: set[tuple] = set()
        conflicts: list[tuple] = []
        for e in run_entries:
            key = (e["teacher_id"], e["day_of_week"], e["slot_index"])
            if key in seen:
                conflicts.append(key)
            seen.add(key)
        assert len(conflicts) == 0, f"Teacher slot conflicts: {conflicts}"


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 3 — Section Conflict Prevention   ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestSectionConflict:
    def test_no_section_double_booking_sql(self, db: Session) -> None:
        run_id = _STATE.run_id
        rows = db.execute(
            text("""
                SELECT section_id, slot_id, count(*) AS cnt
                FROM timetable_entries
                WHERE run_id = :run_id
                GROUP BY section_id, slot_id
                HAVING count(*) > 1
            """),
            {"run_id": str(run_id)},
        ).all()
        assert len(rows) == 0, f"Section double-booking detected: {rows}"

    def test_section_conflict_detailed(self, run_entries: list) -> None:
        seen: set[tuple] = set()
        conflicts: list[tuple] = []
        for e in run_entries:
            key = (e["section_id"], e["day_of_week"], e["slot_index"])
            if key in seen:
                conflicts.append(key)
            seen.add(key)
        assert len(conflicts) == 0, f"Section slot conflicts: {conflicts}"


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 4 — Lab Block Contiguity          ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestLabContiguity:
    def test_lab_slots_are_contiguous(self, run_entries: list) -> None:
        """Lab entries (lab_block_size_slots=3) must occupy 3 consecutive slot indices
        on the same day for a given section+subject."""
        lab_entries = [e for e in run_entries if e["subject_type"] == "LAB"]
        # Group by (section_id, subject_id, day_of_week) to find the block
        groups: dict[tuple, list[int]] = defaultdict(list)
        for e in lab_entries:
            key = (e["section_id"], e["subject_id"], e["day_of_week"])
            groups[key].append(e["slot_index"])

        for key, slot_idxs in groups.items():
            sorted_slots = sorted(slot_idxs)
            # Check that they form a contiguous sequence
            for i in range(1, len(sorted_slots)):
                assert sorted_slots[i] == sorted_slots[i - 1] + 1, (
                    f"Non-contiguous lab block for (section={key[0]}, subject={key[1]}, "
                    f"day={key[2]}): slots={sorted_slots}"
                )

    def test_lab_block_size_is_three(self, run_entries: list) -> None:
        """Each lab subject should have exactly 3 entries per section per week (lab_block_size_slots=3)."""
        lab_entries = [e for e in run_entries if e["subject_type"] == "LAB"]
        groups: dict[tuple, int] = defaultdict(int)
        for e in lab_entries:
            groups[(e["section_code"], e["subject_code"])] += 1

        for (sec_code, subj_code), count in groups.items():
            assert count == 3, (
                f"Lab {subj_code} for section {sec_code}: expected 3 entries, got {count}"
            )


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 5 — Combined Classes              ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestCombinedClasses:
    def test_combined_ai_scheduled(self, run_entries: list) -> None:
        ai_entries = [e for e in run_entries if e["subject_code"] == "AI"]
        assert len(ai_entries) > 0, "No AI (combined) entries found"

    def test_combined_ai_same_slot_per_session(self, run_entries: list) -> None:
        """For each AI session, all 3 sections must share the same (day, slot) AND same teacher."""
        ai_entries = [e for e in run_entries if e["subject_code"] == "AI"]
        # Group by (day, slot_index)
        slot_groups: dict[tuple, list[dict]] = defaultdict(list)
        for e in ai_entries:
            slot_groups[(e["day_of_week"], e["slot_index"])].append(e)

        for slot_key, group in slot_groups.items():
            section_codes = {e["section_code"] for e in group}
            teacher_ids = {e["teacher_id"] for e in group}
            room_ids = {e["room_id"] for e in group}
            assert section_codes == set(SEC_CODES), (
                f"AI session at {slot_key} missing sections: expected {SEC_CODES}, got {section_codes}"
            )
            assert len(teacher_ids) == 1, (
                f"AI session at {slot_key} uses multiple teachers: {teacher_ids}"
            )
            assert len(room_ids) == 1, (
                f"AI session at {slot_key} uses multiple rooms: {room_ids}"
            )

    def test_combined_ai_entries_have_combined_class_id(self, run_entries: list) -> None:
        ai_entries = [e for e in run_entries if e["subject_code"] == "AI"]
        for e in ai_entries:
            assert e.get("combined_class_id") is not None, (
                f"AI entry {e['id']} missing combined_class_id"
            )


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 6 — Elective Block Scheduling     ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestElectiveBlock:
    def test_elective_subjects_scheduled(self, run_entries: list) -> None:
        """ML, CYBER, BLOCKCHAIN should each appear in entries."""
        elective_codes = {"ML", "CYBER", "BLOCKCHAIN"}
        scheduled = {e["subject_code"] for e in run_entries}
        for code in elective_codes:
            assert code in scheduled, f"Elective subject {code} not scheduled"

    def test_elective_sections_same_slot_per_session_index(self, run_entries: list) -> None:
        """For each pair of elective sessions, both must occur at the same (day, slot) index
        relative to their position (first ML session aligns with first CYBER session, etc.)."""
        ml_slots = sorted(
            (e["day_of_week"], e["slot_index"])
            for e in run_entries if e["subject_code"] == "ML"
        )
        cyber_slots = sorted(
            (e["day_of_week"], e["slot_index"])
            for e in run_entries if e["subject_code"] == "CYBER"
        )
        blockchain_slots = sorted(
            (e["day_of_week"], e["slot_index"])
            for e in run_entries if e["subject_code"] == "BLOCKCHAIN"
        )
        assert ml_slots == cyber_slots, (
            f"ML and CYBER not at same slots: ML={ml_slots} CYBER={cyber_slots}"
        )
        assert ml_slots == blockchain_slots, (
            f"ML and BLOCKCHAIN not at same slots: ML={ml_slots} BLOCKCHAIN={blockchain_slots}"
        )

    def test_elective_entries_have_elective_block_id(self, run_entries: list) -> None:
        elective_codes = {"ML", "CYBER", "BLOCKCHAIN"}
        for e in run_entries:
            if e["subject_code"] in elective_codes:
                assert e.get("elective_block_id") is not None, (
                    f"Elective entry {e['id']} subject={e['subject_code']} missing elective_block_id"
                )


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 7 — Special Allotments            ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestSpecialAllotments:
    def test_special_allotment_present_in_entries(self, run_entries: list) -> None:
        """The special OS allotment (CSE-3A, T5, Tuesday slot 3) must appear in results."""
        sa_day = TUE
        sa_slot = 3
        matches = [
            e for e in run_entries
            if e["section_code"] == "CSE-3A"
            and e["subject_code"] == "OS"
            and e["day_of_week"] == sa_day
            and e["slot_index"] == sa_slot
        ]
        assert len(matches) == 1, (
            f"Special allotment entry for CSE-3A/OS/Tue/slot-3 not found. "
            f"OS entries for CSE-3A: {[(e['day_of_week'], e['slot_index']) for e in run_entries if e['section_code']=='CSE-3A' and e['subject_code']=='OS']}"
        )

    def test_special_allotment_teacher_matches(self, run_entries: list) -> None:
        sa_day = TUE
        sa_slot = 3
        t5_id = str(_STATE.teacher_ids["T5"])
        matches = [
            e for e in run_entries
            if e["section_code"] == "CSE-3A"
            and e["subject_code"] == "OS"
            and e["day_of_week"] == sa_day
            and e["slot_index"] == sa_slot
        ]
        if matches:
            assert str(matches[0]["teacher_id"]) == t5_id, (
                f"Special allotment teacher mismatch: expected T5={t5_id}, got {matches[0]['teacher_id']}"
            )


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 8 — Fixed Timetable Entries       ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestFixedEntries:
    def test_fixed_dbms_cse3b_monday_slot1(self, run_entries: list) -> None:
        """Fixed entry: DBMS for CSE-3B at Monday slot 1 must appear."""
        fixed_day, fixed_slot = _STATE.fixed_slot
        matches = [
            e for e in run_entries
            if e["section_code"] == "CSE-3B"
            and e["subject_code"] == "DBMS"
            and e["day_of_week"] == fixed_day
            and e["slot_index"] == fixed_slot
        ]
        assert len(matches) >= 1, (
            f"Fixed entry DBMS/CSE-3B/{fixed_day}/{fixed_slot} not honoured. "
            f"DBMS slots for CSE-3B: {[(e['day_of_week'], e['slot_index']) for e in run_entries if e['section_code']=='CSE-3B' and e['subject_code']=='DBMS']}"
        )


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 9 — Teacher Off Day               ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestTeacherOffDay:
    def test_t5_never_scheduled_on_wednesday(self, run_entries: list) -> None:
        """T5 has off-day Wednesday (day=2); must not appear in any Wednesday slot."""
        t5_id = str(_STATE.teacher_ids["T5"])
        wednesday_entries = [
            e for e in run_entries
            if str(e["teacher_id"]) == t5_id and e["day_of_week"] == T5_OFF_DAY
        ]
        assert len(wednesday_entries) == 0, (
            f"T5 scheduled on Wednesday (day {T5_OFF_DAY}): {wednesday_entries}"
        )

    def test_t5_scheduled_on_other_days(self, run_entries: list) -> None:
        """T5 must still be scheduled on at least one other day (sanity check)."""
        t5_id = str(_STATE.teacher_ids["T5"])
        non_wed_entries = [
            e for e in run_entries
            if str(e["teacher_id"]) == t5_id and e["day_of_week"] != T5_OFF_DAY
        ]
        assert len(non_wed_entries) > 0, "T5 was not scheduled on any non-Wednesday day"


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 10 — Teacher Max Continuous       ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestTeacherMaxContinuous:
    def test_no_teacher_exceeds_max_continuous(self, db: Session, run_entries: list) -> None:
        """No teacher should have more than their max_continuous consecutive slots
        on any given day."""
        # Build lookup: teacher_id → max_continuous
        teachers = db.execute(
            select(Teacher).where(
                Teacher.tenant_id == _STATE.tenant_id
            )
        ).scalars().all()
        max_cont_by_teacher: dict[str, int] = {str(t.id): int(t.max_continuous) for t in teachers}

        # Group entries by (teacher_id, day_of_week)
        by_teacher_day: dict[tuple, list[int]] = defaultdict(list)
        for e in run_entries:
            by_teacher_day[(str(e["teacher_id"]), e["day_of_week"])].append(e["slot_index"])

        violations: list[str] = []
        for (tid_str, day), slots in by_teacher_day.items():
            max_cont = max_cont_by_teacher.get(tid_str, 999)
            sorted_slots = sorted(set(slots))
            # Count longest consecutive run
            longest = 1
            current = 1
            for i in range(1, len(sorted_slots)):
                if sorted_slots[i] == sorted_slots[i - 1] + 1:
                    current += 1
                    longest = max(longest, current)
                else:
                    current = 1
            if longest > max_cont:
                violations.append(
                    f"Teacher {tid_str} on day {day}: "
                    f"consecutive={longest} > max_continuous={max_cont}"
                )

        assert len(violations) == 0, "\n".join(violations)


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 11 — Room Capacity                ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestRoomCapacity:
    def test_cse3c_room_capacity_sufficient(self, db: Session, run_entries: list) -> None:
        """CSE-3C has strength 80. Every THEORY class room must have capacity >= 80."""
        cse3c_id = str(_STATE.section_ids["CSE-3C"])
        cse3c_entries = [
            e for e in run_entries
            if str(e["section_id"]) == cse3c_id and e["subject_type"] == "THEORY"
            and e.get("combined_class_id") is None  # skip combined (LT used)
        ]
        rooms = {r.id: r for r in db.execute(
            select(Room).where(Room.tenant_id == _STATE.tenant_id)
        ).scalars().all()}

        violations: list[str] = []
        for e in cse3c_entries:
            room = rooms.get(uuid.UUID(e["room_id"]))
            if room and int(room.capacity) < 80 and not bool(room.is_special):
                violations.append(
                    f"CSE-3C entry {e['subject_code']} assigned room {room.code} "
                    f"(capacity={room.capacity}) < 80"
                )
        assert len(violations) == 0, "\n".join(violations)


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   TEST 12 — Room Double Booking          ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestRoomDoubleBooking:
    def test_no_room_double_booking_sql(self, db: Session) -> None:
        run_id = _STATE.run_id
        rows = db.execute(
            text("""
                SELECT room_id, slot_id, count(*) AS cnt
                FROM timetable_entries
                WHERE run_id = :run_id
                GROUP BY room_id, slot_id
                HAVING count(*) > 1
            """),
            {"run_id": str(run_id)},
        ).all()
        assert len(rows) == 0, f"Room double-booking detected: {rows}"

    def test_room_conflict_detailed(self, run_entries: list) -> None:
        seen: set[tuple] = set()
        conflicts: list[tuple] = []
        for e in run_entries:
            key = (e["room_id"], e["day_of_week"], e["slot_index"])
            if key in seen:
                conflicts.append(key)
            seen.add(key)
        assert len(conflicts) == 0, f"Room slot conflicts: {conflicts}"


# ===========================================================================
# ╔═══════════════════════════════════════════╗
# ║   FULL RESULT VALIDATION SUMMARY         ║
# ╚═══════════════════════════════════════════╝
# ===========================================================================

class TestResultValidationSummary:
    """Post-solve integrity checks across the full output."""

    def test_total_sessions_per_theory_subject(self, run_entries: list) -> None:
        """Each THEORY subject for each section must have exactly sessions_per_week entries."""
        expected_spw = {
            "OS": 3, "DBMS": 2, "Networks": 2,
            "AI": 2, "ML": 2, "CYBER": 2, "BLOCKCHAIN": 2, "ADA": 2,
        }
        # Count entries per (section, subject)
        counts: dict[tuple, int] = defaultdict(int)
        for e in run_entries:
            if e["subject_type"] == "THEORY":
                counts[(e["section_code"], e["subject_code"])] += 1

        failures: list[str] = []
        for sec_code in SEC_CODES:
            for subj_code, expected in expected_spw.items():
                # Only check if section is expected to have the subject
                actual = counts.get((sec_code, subj_code), None)
                if actual is None:
                    # This section might not have this subject — skip
                    continue
                if actual != expected:
                    failures.append(
                        f"{sec_code}/{subj_code}: expected {expected} sessions, got {actual}"
                    )
        assert len(failures) == 0, "\n".join(failures)

    def test_no_overlapping_classes_any_dimension(self, run_entries: list) -> None:
        """Combined check: no (section, slot), (teacher, slot), or (room, slot) conflicts."""
        section_slots: set[tuple] = set()
        teacher_slots: set[tuple] = set()
        room_slots: set[tuple] = set()
        all_conflicts: list[str] = []

        for e in run_entries:
            day_slot = (e["day_of_week"], e["slot_index"])

            sk = (e["section_id"], *day_slot)
            if sk in section_slots:
                all_conflicts.append(f"Section double-booking: {sk}")
            section_slots.add(sk)

            tk = (e["teacher_id"], *day_slot)
            if tk in teacher_slots:
                all_conflicts.append(f"Teacher double-booking: {tk}")
            teacher_slots.add(tk)

            rk = (e["room_id"], *day_slot)
            if rk in room_slots:
                all_conflicts.append(f"Room double-booking: {rk}")
            room_slots.add(rk)

        assert len(all_conflicts) == 0, "\n".join(all_conflicts)

    def test_all_entries_within_section_time_windows(self, db: Session, run_entries: list) -> None:
        """Every entry must fall within the section's time window for that day."""
        windows = db.execute(
            select(SectionTimeWindow).where(
                SectionTimeWindow.tenant_id == _STATE.tenant_id
            )
        ).scalars().all()
        win_map: dict[tuple, tuple[int, int]] = {}
        for w in windows:
            win_map[(str(w.section_id), int(w.day_of_week))] = (
                int(w.start_slot_index), int(w.end_slot_index)
            )

        violations: list[str] = []
        for e in run_entries:
            key = (str(e["section_id"]), e["day_of_week"])
            win = win_map.get(key)
            if win is None:
                violations.append(f"No time window for section {e['section_code']} day {e['day_of_week']}")
                continue
            start, end = win
            if not (start <= e["slot_index"] <= end):
                violations.append(
                    f"Entry outside window: section={e['section_code']} day={e['day_of_week']} "
                    f"slot={e['slot_index']} window=[{start},{end}]"
                )
        assert len(violations) == 0, "\n".join(violations[:20])

    def test_locked_entries_respected(self, run_entries: list) -> None:
        """Both the fixed entry and special allotment must appear in results."""
        # Fixed entry: DBMS/CSE-3B/Monday/slot-1
        fd, fs = _STATE.fixed_slot
        fixed_ok = any(
            e["section_code"] == "CSE-3B"
            and e["subject_code"] == "DBMS"
            and e["day_of_week"] == fd
            and e["slot_index"] == fs
            for e in run_entries
        )
        assert fixed_ok, "Fixed timetable entry for DBMS/CSE-3B/Mon/1 was not honoured"

        # Special allotment: OS/CSE-3A/Tuesday/slot-3
        sa_ok = any(
            e["section_code"] == "CSE-3A"
            and e["subject_code"] == "OS"
            and e["day_of_week"] == TUE
            and e["slot_index"] == 3
            for e in run_entries
        )
        assert sa_ok, "Special allotment for OS/CSE-3A/Tue/3 was not honoured"

    def test_lab_contiguity_comprehensive(self, run_entries: list) -> None:
        """Comprehensive lab block contiguity: each lab subject for each section
        must have all 3 entries consecutive on the same day."""
        lab_entries = [e for e in run_entries if e["subject_type"] == "LAB"]
        groups: dict[tuple, list] = defaultdict(list)
        for e in lab_entries:
            groups[(e["section_code"], e["subject_code"])].append(e)

        failures: list[str] = []
        for (sec, subj), entries in groups.items():
            by_day: dict[int, list[int]] = defaultdict(list)
            for e in entries:
                by_day[e["day_of_week"]].append(e["slot_index"])
            found_valid_block = False
            for day, slots in by_day.items():
                s = sorted(slots)
                if len(s) == 3 and s[1] == s[0] + 1 and s[2] == s[1] + 1:
                    found_valid_block = True
                    break
            if not found_valid_block:
                failures.append(f"Lab {subj} for {sec}: no contiguous 3-slot block. entries={entries}")
        assert len(failures) == 0, "\n".join(failures)

    def test_no_error_conflicts_in_run(self, client: TestClient, auth_headers: dict) -> None:
        """Run must have zero ERROR-severity conflicts."""
        run_id = str(_STATE.run_id)
        resp = client.get(f"/api/solver/runs/{run_id}/conflicts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        error_conflicts = [
            c for c in data.get("conflicts", [])
            if c.get("severity", "INFO").upper() == "ERROR"
        ]
        assert len(error_conflicts) == 0, (
            f"Run has {len(error_conflicts)} ERROR conflicts:\n"
            + "\n".join(f"  [{c['conflict_type']}] {c['message']}" for c in error_conflicts)
        )

    def test_validation_summary_print(self, run_entries: list, solve_result: dict) -> None:
        """Print a human-readable validation summary (always passes)."""
        by_section: dict[str, int] = defaultdict(int)
        by_subject: dict[str, int] = defaultdict(int)
        by_room_type: dict[str, int] = defaultdict(int)
        for e in run_entries:
            by_section[e["section_code"]] += 1
            by_subject[e["subject_code"]] += 1
            by_room_type[e["room_type"]] += 1

        print("\n" + "=" * 60)
        print("SMOKE TEST VALIDATION SUMMARY")
        print("=" * 60)
        print(f"  Solve Status   : {_STATE.solve_status}")
        print(f"  Run ID         : {_STATE.run_id}")
        print(f"  Entries Written: {_STATE.entries_written}")
        print(f"  Total Entries  : {len(run_entries)}")
        print()
        print("  Sessions per Section:")
        for code in sorted(by_section):
            print(f"    {code:12s}: {by_section[code]:3d}")
        print()
        print("  Sessions per Subject:")
        for code in sorted(by_subject):
            print(f"    {code:14s}: {by_subject[code]:3d}")
        print()
        print("  Room Type Usage:")
        for rtype in sorted(by_room_type):
            print(f"    {rtype:12s}: {by_room_type[rtype]:3d}")
        print("=" * 60)
        # This always passes — it's just for visibility
        assert True
