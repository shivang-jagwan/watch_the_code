from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
import uuid
import logging

from core.security import decode_token
from core.config import settings
from core.db import get_db
from core.tenancy import set_current_tenant_id
from models.user import User


logger = logging.getLogger(__name__)


bearer_scheme = HTTPBearer(auto_error=False)


def _extract_tokens(request: Request, creds: HTTPAuthorizationCredentials | None) -> tuple[str | None, str | None]:
    bearer_token = (creds.credentials if creds is not None and creds.credentials else None)
    cookie_token = request.cookies.get("access_token") or None
    return bearer_token, cookie_token


def _decode_payload(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    cached = getattr(request.state, "current_user", None)
    if isinstance(cached, User):
        return cached

    bearer_token, cookie_token = _extract_tokens(request, creds)
    auth_header_present = bool(request.headers.get("authorization"))
    logger.debug(
        "auth.check header_present=%s bearer_present=%s cookie_present=%s path=%s",
        auth_header_present,
        bool(bearer_token),
        bool(cookie_token),
        request.url.path,
    )
    if not bearer_token and not cookie_token:
        logger.debug("auth.missing_token path=%s", request.url.path)
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    # Accept either Authorization Bearer or HttpOnly access_token cookie.
    # If bearer exists but is stale/invalid, fall back to cookie token.
    payload = _decode_payload(bearer_token)
    if payload is None:
        payload = _decode_payload(cookie_token)
    if payload is None:
        logger.debug("auth.token_invalid path=%s", request.url.path)
        raise HTTPException(status_code=401, detail="INVALID_TOKEN")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="INVALID_TOKEN")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="INVALID_TOKEN")

    user = db.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=401, detail="INVALID_TOKEN")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="USER_DISABLED")

    mode = (settings.tenant_mode or "shared").strip().lower()
    if mode == "per_tenant":
        token_tenant_id = payload.get("tenant_id")
        user_tenant_id = getattr(user, "tenant_id", None)
        if not token_tenant_id or user_tenant_id is None:
            logger.debug(
                "auth.tenant_missing token_tenant_present=%s user_tenant_present=%s path=%s",
                bool(token_tenant_id),
                user_tenant_id is not None,
                request.url.path,
            )
            raise HTTPException(status_code=401, detail="INVALID_TOKEN")
        if str(user_tenant_id) != str(token_tenant_id):
            logger.warning(
                "auth.tenant_mismatch user_id=%s token_tenant_id=%s user_tenant_id=%s path=%s",
                str(user.id),
                str(token_tenant_id),
                str(user_tenant_id),
                request.url.path,
            )
            raise HTTPException(status_code=401, detail="INVALID_TOKEN")

    # Always set the tenant context once the user is authenticated.
    # This makes DB-layer tenant injection work even if an endpoint doesn't call get_tenant_id().
    if mode == "shared":
        set_current_tenant_id(None)
    elif mode == "per_tenant":
        set_current_tenant_id(getattr(user, "tenant_id", None))
    else:
        # per_user
        set_current_tenant_id(getattr(user, "tenant_id", None) or user.id)

    request.state.current_user = user
    request.state.auth_payload = payload
    return user


def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    payload = getattr(request.state, "auth_payload", None)
    if not isinstance(payload, dict):
        payload = {}

    role = (current_user.role or "").upper()
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="NOT_AUTHORIZED")
    return payload


def get_tenant_id(
    current_user: User = Depends(get_current_user),
) -> uuid.UUID | None:
    """Return the tenant_id used to scope data.

    - shared mode: returns None (no scoping)
    - per_user mode: returns current_user.tenant_id if set, else current_user.id
    """

    mode = (settings.tenant_mode or "shared").strip().lower()
    if mode == "shared":
        set_current_tenant_id(None)
        return None
    tenant_id = getattr(current_user, "tenant_id", None)
    if mode == "per_tenant":
        if tenant_id is None:
            raise HTTPException(status_code=403, detail="TENANT_NOT_SET")
        set_current_tenant_id(tenant_id)
        return tenant_id
    # legacy: per_user
    resolved = tenant_id or current_user.id
    set_current_tenant_id(resolved)
    return resolved
