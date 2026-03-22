from __future__ import annotations

import logging
import time
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_current_user
from core.config import settings
from core.db import get_db
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from models.tenant import Tenant
from models.user import User
from schemas.auth import LoginRequest, LoginResponse, MeResponse, SignupRequest, SignupResponse


router = APIRouter()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory rate limiting for login (per-worker).
# ---------------------------------------------------------------------------
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS_PER_KEY = 12
_login_attempts: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
_RATE_LIMIT_CLEANUP_INTERVAL = 300  # prune stale entries every 5 min
_last_rate_limit_cleanup = 0.0


def _rate_limit_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{username.lower().strip()}"


def _prune_stale_entries(now: float) -> None:
    """Remove entries older than the rate-limit window to prevent unbounded growth."""
    global _last_rate_limit_cleanup
    if now - _last_rate_limit_cleanup < _RATE_LIMIT_CLEANUP_INTERVAL:
        return
    _last_rate_limit_cleanup = now
    stale_keys = [
        k for k, v in _login_attempts.items()
        if not v or (now - max(v)) > _LOGIN_WINDOW_SECONDS
    ]
    for k in stale_keys:
        _login_attempts.pop(k, None)


def _enforce_login_rate_limit(request: Request, username: str) -> None:
    key = _rate_limit_key(request, username)
    now = time.time()
    with _rate_limit_lock:
        history = _login_attempts.get(key, [])
        history = [t for t in history if now - t < _LOGIN_WINDOW_SECONDS]
        history.append(now)
        _login_attempts[key] = history
        _prune_stale_entries(now)
    if len(history) > _LOGIN_MAX_ATTEMPTS_PER_KEY:
        raise HTTPException(status_code=429, detail="RATE_LIMITED")


def _resolve_tenant_id_for_auth(
    db: Session,
    tenant_hint: str | None,
    *,
    username_hint: str | None = None,
) -> str | None:
    mode = (settings.tenant_mode or "shared").strip().lower()
    if mode != "per_tenant":
        return None

    hint = (tenant_hint or "").strip()
    if not hint:
        # Backward-compatible UX: allow login without a tenant field.
        # If the username uniquely exists in exactly one tenant, infer it.
        # If it exists in multiple tenants, force the caller to specify tenant.
        un = (username_hint or "").strip()
        if un:
            rows = db.execute(
                select(User.tenant_id).where(func.lower(User.username) == func.lower(un))
            ).all()
            tenant_ids = [str(r[0]) for r in rows if r and r[0] is not None]
            distinct = sorted(set(tenant_ids))
            if len(distinct) == 1:
                return distinct[0]
            if len(distinct) > 1:
                raise HTTPException(status_code=401, detail="TENANT_REQUIRED")
        # No tenant hint and no unique user inference.
        raise HTTPException(status_code=401, detail="TENANT_REQUIRED")

    q = select(Tenant.id).where(func.lower(Tenant.slug) == func.lower(hint))
    row = db.execute(q).first()
    if row is None:
        q2 = select(Tenant.id).where(func.lower(Tenant.name) == func.lower(hint))
        row = db.execute(q2).first()
    if row is None:
        raise HTTPException(status_code=401, detail="INVALID_TENANT")
    return str(row[0])


def _set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    """Set httponly access_token and refresh_token cookies on the response."""
    secure_cookie = settings.environment.lower() == "production"
    samesite = (settings.cookie_samesite or "lax").lower().strip()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure_cookie,
        samesite=samesite,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure_cookie,
        samesite=samesite,
        max_age=settings.refresh_token_expire_minutes * 60,
        path="/api/auth",  # Scoped: only sent to auth endpoints
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    username = str(payload.username or "").strip()
    _enforce_login_rate_limit(request, username)

    tenant_id_for_auth = _resolve_tenant_id_for_auth(db, payload.tenant, username_hint=username)

    ip = request.client.host if request.client else "unknown"

    # Be forgiving about casing/whitespace on input.
    q_user = select(User).where(func.lower(User.username) == func.lower(username))
    if tenant_id_for_auth is not None:
        q_user = q_user.where(User.tenant_id == tenant_id_for_auth)
    user = db.execute(q_user).scalar_one_or_none()
    if user is None:
        logger.warning(
            "Login failed (unknown user) ip=%s username=%r username_len=%d",
            ip,
            username,
            len(username),
        )
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
    if not user.is_active:
        logger.warning(
            "Login failed (disabled user) ip=%s username=%r username_len=%d",
            ip,
            username,
            len(username),
        )
        raise HTTPException(status_code=403, detail="USER_DISABLED")

    password = str(payload.password or "")
    password_stripped = password.strip()
    password_has_outer_whitespace = password != password_stripped

    password_ok = verify_password(password, user.password_hash)
    if not password_ok and password_has_outer_whitespace:
        # Common UX issue: copy/paste adds a trailing newline/space.
        password_ok = verify_password(password_stripped, user.password_hash)
        if password_ok:
            logger.warning(
                "Login password had surrounding whitespace; accepted after trimming ip=%s username=%r",
                ip,
                username,
            )

    if not password_ok:
        logger.warning(
            "Login failed (bad password) ip=%s username=%r username_len=%d password_len=%d outer_ws=%s",
            ip,
            username,
            len(username),
            len(password),
            password_has_outer_whitespace,
        )
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")

    mode = (settings.tenant_mode or "shared").strip().lower()
    token_tenant_id: str | None
    if mode == "per_tenant":
        token_tenant_id = str(getattr(user, "tenant_id", None) or "") or None
    elif mode == "per_user":
        token_tenant_id = str(getattr(user, "tenant_id", None) or user.id)
    else:
        token_tenant_id = None

    token = create_access_token(
        user_id=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=token_tenant_id,
    )
    refresh = create_refresh_token(
        user_id=str(user.id),
        tenant_id=token_tenant_id,
    )

    _set_auth_cookies(response, token, refresh)

    return LoginResponse(ok=True, access_token=token)


@router.post("/signup", response_model=SignupResponse)
def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SignupResponse:
    is_production = settings.environment.lower() == "production"
    if is_production and not settings.allow_signup:
        raise HTTPException(status_code=403, detail="SIGNUP_DISABLED")

    username = str(payload.username or "").strip()
    _enforce_login_rate_limit(request, username)

    tenant_id_for_auth = _resolve_tenant_id_for_auth(db, payload.tenant)

    if not username:
        raise HTTPException(status_code=422, detail="INVALID_USERNAME")

    ip = request.client.host if request.client else "unknown"

    # Enforce case-insensitive uniqueness to avoid multiple rows that would break login.
    q_existing = select(User.id).where(func.lower(User.username) == func.lower(username))
    if tenant_id_for_auth is not None:
        q_existing = q_existing.where(User.tenant_id == tenant_id_for_auth)
    existing = db.execute(q_existing).scalar_one_or_none()
    if existing is not None:
        logger.warning("Signup rejected (username taken) ip=%s username=%r", ip, username)
        raise HTTPException(status_code=409, detail="USERNAME_TAKEN")

    password_hash = hash_password(str(payload.password or ""))
    role = (settings.signup_default_role or "USER").upper().strip()
    if role not in {"ADMIN", "USER"}:
        raise HTTPException(status_code=500, detail="INVALID_SIGNUP_DEFAULT_ROLE")

    # Some legacy DBs have a NOT NULL `name` column; insert into it when present.
    has_name = (
        db.execute(
            text(
                """
                select 1
                from information_schema.columns
                where table_schema='public'
                  and table_name='users'
                  and column_name='name'
                limit 1
                """.strip()
            )
        ).first()
        is not None
    )

    try:
        if has_name:
            row = db.execute(
                text(
                    """
                    insert into users (name, username, password_hash, role, is_active, tenant_id)
                    values (:name, :username, :password_hash, :role, true, :tenant_id)
                    returning id, username, role
                    """.strip()
                ),
                {
                    "name": username,
                    "username": username,
                    "password_hash": password_hash,
                    "role": role,
                    "tenant_id": tenant_id_for_auth,
                },
            ).first()
        else:
            row = db.execute(
                text(
                    """
                    insert into users (username, password_hash, role, is_active, tenant_id)
                    values (:username, :password_hash, :role, true, :tenant_id)
                    returning id, username, role
                    """.strip()
                ),
                {
                    "username": username,
                    "password_hash": password_hash,
                    "role": role,
                    "tenant_id": tenant_id_for_auth,
                },
            ).first()

        if row is None:
            raise HTTPException(status_code=500, detail="SIGNUP_FAILED")

        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("Signup rejected (integrity error) ip=%s username=%r", ip, username)
        raise HTTPException(status_code=409, detail="USERNAME_TAKEN")

    user_id = str(row[0])
    created_username = str(row[1])
    created_role = str(row[2])

    # Auto-login after signup (sets the same cookie as /login).
    mode = (settings.tenant_mode or "shared").strip().lower()
    token_tenant_id: str | None
    if mode == "per_tenant":
        token_tenant_id = tenant_id_for_auth
    elif mode == "per_user":
        token_tenant_id = user_id
    else:
        token_tenant_id = None

    token = create_access_token(
        user_id=user_id,
        username=created_username,
        role=created_role,
        tenant_id=token_tenant_id,
    )
    refresh = create_refresh_token(
        user_id=user_id,
        tenant_id=token_tenant_id,
    )

    _set_auth_cookies(response, token, refresh)

    logger.info("Signup success ip=%s username=%r", ip, created_username)
    return SignupResponse(ok=True)


@router.post("/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/api/auth")
    return {"ok": True}


@router.post("/refresh")
def refresh_access_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Exchange a valid refresh token for a new access + refresh token pair."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="NO_REFRESH_TOKEN")

    try:
        payload = decode_refresh_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

    import uuid as _uuid

    try:
        user_uuid = _uuid.UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

    user = db.get(User, user_uuid)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN")

    # Derive tenant_id using the same logic as login
    mode = (settings.tenant_mode or "shared").strip().lower()
    token_tenant_id: str | None
    if mode == "per_tenant":
        token_tenant_id = str(getattr(user, "tenant_id", None) or "") or None
    elif mode == "per_user":
        token_tenant_id = str(getattr(user, "tenant_id", None) or user.id)
    else:
        token_tenant_id = None

    new_access = create_access_token(
        user_id=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=token_tenant_id,
    )
    new_refresh = create_refresh_token(
        user_id=str(user.id),
        tenant_id=token_tenant_id,
    )

    _set_auth_cookies(response, new_access, new_refresh)

    return {"ok": True, "access_token": new_access}


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        id=current_user.id,
        tenant_id=getattr(current_user, "tenant_id", None),
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
