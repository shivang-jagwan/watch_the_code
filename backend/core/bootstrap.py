from __future__ import annotations

import logging

from sqlalchemy import text

from core.config import settings
from core.db import ENGINE
from core.security import hash_password


logger = logging.getLogger(__name__)


def _ensure_users_schema(conn) -> None:
    # Keep this idempotent: safe across deploys.
    statements = [
        "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug VARCHAR(100) UNIQUE NOT NULL,
            name VARCHAR(200) NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NULL,
            username VARCHAR(100) NOT NULL,
            password_hash TEXT NOT NULL,
            role VARCHAR(20) DEFAULT 'ADMIN',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT now()
        );
        """,
        # Legacy compatibility: some DBs already have public.users with older columns.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(100);",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;",
        # Backfill username from legacy `name` if present.
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='users' AND column_name='name'
          ) THEN
            EXECUTE 'UPDATE users SET username = COALESCE(username, name::text) WHERE username IS NULL';
          END IF;
        END $$;
        """,
                # If an old bootstrap created a global-unique username index, drop it.
                "DROP INDEX IF EXISTS ux_users_username;",
                # Ensure we have tenant-aware uniqueness.
                "CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id);",
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_shared ON users (lower(username)) WHERE tenant_id IS NULL;",
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_tenant ON users (tenant_id, lower(username)) WHERE tenant_id IS NOT NULL;",
                # Best-effort FK (Postgres doesn't support IF NOT EXISTS for constraints).
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'fk_users_tenant_id'
                    ) THEN
                        ALTER TABLE users
                        ADD CONSTRAINT fk_users_tenant_id
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id)
                        ON DELETE CASCADE;
                    END IF;
                END $$;
                """,
    ]

    for s in statements:
        conn.execute(text(s))


def _seed_admin_if_configured(conn) -> None:
    username = settings.seed_admin_username
    password = settings.seed_admin_password
    if not username or not password:
        return

    tenant_id = None
    if settings.tenant_mode == "per_tenant":
        # Ensure the default tenant exists.
        row = conn.execute(
            text("select id from tenants where slug = :s limit 1"),
            {"s": "default"},
        ).first()
        if row is None:
            row = conn.execute(
                text(
                    """
                    insert into tenants (slug, name)
                    values (:slug, :name)
                    returning id
                    """.strip()
                ),
                {"slug": "default", "name": "Default College"},
            ).first()
        tenant_id = row[0] if row is not None else None

        existing = conn.execute(
                text(
                        """
                        select 1
                        from users
                        where lower(username) = lower(:u)
                            and (
                                (:tid is null and tenant_id is null)
                                or tenant_id = :tid
                            )
                        limit 1
                        """.strip()
                ),
                {"u": username, "tid": tenant_id},
        ).first()
    if existing is not None:
        return

    password_hash = hash_password(password)

    has_name = (
        conn.execute(
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

    cols = ["username", "password_hash", "role", "is_active"]
    vals = [":username", ":password_hash", "'ADMIN'", "true"]
    params = {"username": username, "password_hash": password_hash}

    if tenant_id is not None:
        cols.insert(0, "tenant_id")
        vals.insert(0, ":tenant_id")
        params["tenant_id"] = tenant_id

    if has_name:
        cols.insert(0, "name")
        vals.insert(0, ":name")
        params["name"] = username

    conn.execute(
        text(
            f"""
            insert into users ({', '.join(cols)})
            values ({', '.join(vals)})
            """.strip()
        ),
        params,
    )

    logger.warning(
        "Seeded initial admin user from env (username=%r). Change the password after first login.",
        username,
    )


def bootstrap_auth() -> None:
    """Best-effort auth bootstrap for production deployments.

    - Ensures `users` table exists and has required columns.
    - Optionally seeds an admin user if SEED_ADMIN_USERNAME + SEED_ADMIN_PASSWORD are set.

    This function is safe to run on every startup.
    """

    with ENGINE.begin() as conn:
        _ensure_users_schema(conn)
        _seed_admin_if_configured(conn)
        _ensure_incremental_columns(conn)


def _ensure_incremental_columns(conn) -> None:
    """Apply additive column migrations that are safe to run on every startup.

    Each statement uses ``ADD COLUMN IF NOT EXISTS`` so re-running is harmless.
    Add new columns here whenever a migration adds a nullable/defaulted column.
    """
    statements = [
        # Migration 025 — special rooms
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS is_special BOOLEAN NOT NULL DEFAULT FALSE;",
        # Migration 035 — teacher time windows (table; no extra column needed here)
        # Migration 036 — lunch break flag on time slots
        "ALTER TABLE time_slots ADD COLUMN IF NOT EXISTS is_lunch_break BOOLEAN NOT NULL DEFAULT FALSE;",
    ]
    for s in statements:
        try:
            conn.execute(text(s))
        except Exception:
            # Table may not exist yet (fresh DB); skip silently.
            pass
