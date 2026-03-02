from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic.aliases import AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]

_config_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8")

    database_url: str

    # Auth
    jwt_secret_key: str = Field(
        validation_alias=AliasChoices("jwt_secret_key", "jwt_secret", "JWT_SECRET_KEY", "JWT_SECRET")
    )
    jwt_algorithm: str = Field(default="HS256", validation_alias=AliasChoices("jwt_algorithm", "JWT_ALGORITHM"))
    access_token_expire_minutes: int = Field(
        default=480,
        validation_alias=AliasChoices("access_token_expire_minutes", "ACCESS_TOKEN_EXPIRE_MINUTES"),
    )

    # Refresh tokens — long-lived token used to rotate short-lived access tokens.
    refresh_token_expire_minutes: int = Field(
        default=10080,  # 7 days
        validation_alias=AliasChoices("refresh_token_expire_minutes", "REFRESH_TOKEN_EXPIRE_MINUTES"),
    )
    refresh_token_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "refresh_token_secret_key",
            "REFRESH_TOKEN_SECRET_KEY",
            "refresh_token_secret",
            "REFRESH_TOKEN_SECRET",
        ),
    )

    cookie_samesite: str = Field(
        default="lax",
        validation_alias=AliasChoices("cookie_samesite", "COOKIE_SAMESITE"),
    )

    allow_signup: bool = Field(
        default=True,
        validation_alias=AliasChoices("allow_signup", "ALLOW_SIGNUP"),
    )

    signup_default_role: str = Field(
        default="USER",
        validation_alias=AliasChoices("signup_default_role", "SIGNUP_DEFAULT_ROLE"),
    )

    # Optional production bootstrap (recommended): seed an initial admin user.
    # Only used if BOTH username + password are provided.
    seed_admin_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "seed_admin_username",
            "SEED_ADMIN_USERNAME",
            "ADMIN_SEED_USERNAME",
        ),
    )
    seed_admin_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "seed_admin_password",
            "SEED_ADMIN_PASSWORD",
            "ADMIN_SEED_PASSWORD",
        ),
    )

    # Runtime
    environment: str = Field(default="development", validation_alias=AliasChoices("environment", "ENVIRONMENT"))
    frontend_origin: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("frontend_origin", "FRONTEND_ORIGIN"),
    )

    # Solver
    # When enabled, the solver fails fast instead of persisting conflicting room assignments.
    solver_strict_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("solver_strict_mode", "SOLVER_STRICT_MODE"),
    )

    # Multi-tenant / data isolation
    # - shared: all admins see the same data (current behavior)
    # - per_user: data is scoped to the current user's tenant (default: user.id)
    # - per_tenant: data is scoped to current_user.tenant_id (strict isolation)
    tenant_mode: str = Field(
        default="shared",
        validation_alias=AliasChoices("tenant_mode", "TENANT_MODE"),
    )

    @field_validator("frontend_origin")
    @classmethod
    def _normalize_frontend_origin(cls, v: str) -> str:
        # Render/Vercel UI often leads people to paste a trailing slash.
        # Starlette CORS expects the Origin to match exactly (no trailing slash).
        return v.strip().rstrip("/")

    @field_validator("cookie_samesite")
    @classmethod
    def _normalize_cookie_samesite(cls, v: str) -> str:
        return (v or "lax").strip().lower()

    @field_validator("signup_default_role")
    @classmethod
    def _normalize_signup_default_role(cls, v: str) -> str:
        return (v or "USER").strip().upper()

    @field_validator("tenant_mode")
    @classmethod
    def _normalize_tenant_mode(cls, v: str) -> str:
        v = (v or "shared").strip().lower()
        if v not in {"shared", "per_user", "per_tenant"}:
            raise ValueError("TENANT_MODE must be 'shared', 'per_user', or 'per_tenant'")
        return v

    @field_validator("seed_admin_username")
    @classmethod
    def _normalize_seed_admin_username(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("seed_admin_password")
    @classmethod
    def _normalize_seed_admin_password(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Intentionally do not strip whitespace here: passwords can contain spaces.
        return v

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        if len(self.jwt_secret_key) < 32:
            if self.environment.lower() == "production":
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in production"
                )
            _config_logger.warning(
                "JWT_SECRET_KEY is shorter than 32 characters — acceptable for dev only"
            )
        return self

    @property
    def effective_refresh_secret(self) -> str:
        """Refresh token secret: separate key if configured, otherwise derived from JWT secret."""
        return self.refresh_token_secret_key or (self.jwt_secret_key + ":refresh")


settings = Settings()
