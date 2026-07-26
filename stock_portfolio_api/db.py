from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: SimpleConnectionPool | None = None


def _get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable must be set")
        _pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
    return _pool


@contextmanager
def get_cursor():
    """
    Yield a dict-cursor for a single unit of work. Commits if the block
    completes normally, rolls back if it raises.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db() -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email_verified BOOLEAN NOT NULL DEFAULT FALSE,
                verification_token TEXT,
                verification_token_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


def create_user(
    email: str,
    password_hash: str,
    verification_token: str,
    verification_token_expires_at: datetime,
) -> dict[str, Any]:
    """
    Raises psycopg2.errors.UniqueViolation if the email is already taken.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO users
                (email, password_hash, verification_token, verification_token_expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id, email, email_verified
            """,
            (email, password_hash, verification_token, verification_token_expires_at),
        )
        return dict(cur.fetchone())


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_verification_token(token: str) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE verification_token = %s", (token,))
        row = cur.fetchone()
        return dict(row) if row else None


def mark_email_verified(user_id: int) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET email_verified = TRUE,
                verification_token = NULL,
                verification_token_expires_at = NULL
            WHERE id = %s
            """,
            (user_id,),
        )


def set_verification_token(
    user_id: int, token: str, expires_at: datetime
) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET verification_token = %s, verification_token_expires_at = %s
            WHERE id = %s
            """,
            (token, expires_at, user_id),
        )
