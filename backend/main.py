from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, Response as StarletteResponse

from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError

from api.router import api_router
from core.config import settings
from core.db import DatabaseUnavailableError, ENGINE, is_transient_db_connectivity_error
from core.logging import setup_logging


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers into every response."""

    async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
        response: StarletteResponse = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        if settings.environment.lower() == "production":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        return response


def create_app() -> FastAPI:
    setup_logging(environment=settings.environment)
    is_production = settings.environment.lower() == "production"
    app = FastAPI(
        title="Timetable Generator API",
        version="0.1.0",
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    @app.exception_handler(DatabaseUnavailableError)
    def _db_unavailable(_request, _exc: DatabaseUnavailableError):
        logger.warning("Database unavailable (503)", exc_info=_exc)
        return JSONResponse(
            status_code=503,
            content={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Database temporarily unavailable. Please retry.",
            },
        )

    @app.exception_handler(SAOperationalError)
    def _sqlalchemy_operational_error(_request, exc: SAOperationalError):
        if is_transient_db_connectivity_error(exc):
            logger.warning("Database transient connectivity error (503)", exc_info=exc)
            return JSONResponse(
                status_code=503,
                content={
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Database temporarily unavailable. Please retry.",
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "code": "DATABASE_ERROR",
                "message": "Database operation failed.",
            },
        )

    # Optional driver-specific exceptions (best-effort, no hard dependency).
    try:
        import psycopg2  # type: ignore

        @app.exception_handler(psycopg2.OperationalError)  # type: ignore[attr-defined]
        def _psycopg2_operational_error(_request, exc: Exception):
            if is_transient_db_connectivity_error(exc):
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Database temporarily unavailable. Please retry.",
                    },
                )
            return JSONResponse(
                status_code=500,
                content={
                    "code": "DATABASE_ERROR",
                    "message": "Database operation failed.",
                },
            )
    except Exception:
        pass

    try:
        import asyncpg  # type: ignore

        @app.exception_handler(asyncpg.PostgresError)  # type: ignore[attr-defined]
        def _asyncpg_error(_request, exc: Exception):
            if is_transient_db_connectivity_error(exc):
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Database temporarily unavailable. Please retry.",
                    },
                )
            return JSONResponse(
                status_code=500,
                content={
                    "code": "DATABASE_ERROR",
                    "message": "Database operation failed.",
                },
            )
    except Exception:
        pass

    allow_origins = [settings.frontend_origin]
    allow_origin_regex = None
    if not is_production:
        # Dev-friendly: allow the configured origin and any localhost port.
        allow_origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
        allow_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # Security headers — must be added *after* CORS so it wraps outer.
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health")
    def health() -> dict:
        # Always respond; reflect DB availability without crashing.
        db_status = "ok"
        try:
            with ENGINE.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_status = "down"

        return {"app": "ok", "database": db_status}

    @app.on_event("startup")
    def _startup_bootstrap() -> None:
        # Best-effort: don't block app boot if DB is temporarily down.
        try:
            from core.bootstrap import bootstrap_auth

            bootstrap_auth()
        except Exception:
            logger.exception("Auth bootstrap failed")

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
