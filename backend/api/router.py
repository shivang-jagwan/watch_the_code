from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import require_admin
from api.routes import admin_v2 as admin, auth, curriculum, manual_editor, programs, rooms, sections, solver, subjects, teachers, timetable


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Protect every non-auth route.
_protected = [Depends(require_admin)]
api_router.include_router(programs.router, prefix="/programs", tags=["programs"], dependencies=_protected)
api_router.include_router(subjects.router, prefix="/subjects", tags=["subjects"], dependencies=_protected)
api_router.include_router(sections.router, prefix="/sections", tags=["sections"], dependencies=_protected)
api_router.include_router(rooms.router, prefix="/rooms", tags=["rooms"], dependencies=_protected)
api_router.include_router(curriculum.router, prefix="/curriculum", tags=["curriculum"], dependencies=_protected)
api_router.include_router(teachers.router, prefix="/teachers", tags=["teachers"], dependencies=_protected)
api_router.include_router(timetable.router, prefix="/timetable", tags=["timetable"], dependencies=_protected)
api_router.include_router(solver.router, prefix="/solver", tags=["solver"], dependencies=_protected)
api_router.include_router(admin.router, prefix="/admin", tags=["admin"], dependencies=_protected)
api_router.include_router(manual_editor.router, prefix="/manual-editor", tags=["manual-editor"], dependencies=_protected)
