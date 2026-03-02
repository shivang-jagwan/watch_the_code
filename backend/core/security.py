from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import jwt

from core.config import settings



def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(*, user_id: str, username: str, role: str, tenant_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    expires_minutes = int(settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "username": username,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, user_id: str, tenant_id: str | None) -> str:
    """Create a long-lived refresh token (carries only identity, no role)."""
    now = datetime.now(timezone.utc)
    expires_minutes = int(settings.refresh_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.effective_refresh_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode an access token. Rejects refresh tokens used as access tokens."""
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    # Backward compat: tokens minted before the "type" claim was added are treated as access tokens.
    if payload.get("type", "access") != "access":
        raise ValueError("Token type mismatch: expected access token")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decode a refresh token. Rejects access tokens used as refresh tokens."""
    payload = jwt.decode(token, settings.effective_refresh_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "refresh":
        raise ValueError("Token type mismatch: expected refresh token")
    return payload
