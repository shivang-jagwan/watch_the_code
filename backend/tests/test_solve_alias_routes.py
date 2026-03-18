from __future__ import annotations

from fastapi import HTTPException

from api.routes.solve_alias import _resolve_program_code


class _FakeProgram:
    def __init__(self, code: str) -> None:
        self.code = code


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeDb:
    def __init__(self, values):
        self._values = values

    def execute(self, _query):
        return _FakeResult(self._values)


def test_resolve_program_code_prefers_requested() -> None:
    db = _FakeDb([_FakeProgram("CSE")])
    assert _resolve_program_code(db, requested_program_code="ECE", tenant_id=None) == "ECE"


def test_resolve_program_code_single_program_auto_selects() -> None:
    db = _FakeDb([_FakeProgram("CSE")])
    assert _resolve_program_code(db, requested_program_code=None, tenant_id=None) == "CSE"


def test_resolve_program_code_fails_on_multiple_without_request() -> None:
    db = _FakeDb([_FakeProgram("CSE"), _FakeProgram("ECE")])
    try:
        _resolve_program_code(db, requested_program_code=None, tenant_id=None)
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_resolve_program_code_fails_on_empty() -> None:
    db = _FakeDb([])
    try:
        _resolve_program_code(db, requested_program_code=None, tenant_id=None)
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404
