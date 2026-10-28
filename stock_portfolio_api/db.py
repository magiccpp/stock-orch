from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import pandas as pd
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

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                name TEXT PRIMARY KEY,
                description TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolios (
                id SERIAL PRIMARY KEY,
                model_name TEXT NOT NULL REFERENCES models(name) ON DELETE CASCADE,
                portfolio_date DATE NOT NULL,
                filename TEXT NOT NULL,
                portfolio_data JSONB NOT NULL,
                returns_until_next_change JSONB,
                return_until_date DATE,
                is_current_portfolio BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE (model_name, filename)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_portfolios_model_date "
            "ON portfolios (model_name, portfolio_date)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_prices (
                symbol TEXT NOT NULL,
                price_date DATE NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (symbol, price_date)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_prices_date "
            "ON stock_prices (price_date)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS model_returns (
                model_name TEXT NOT NULL REFERENCES models(name) ON DELETE CASCADE,
                return_date DATE NOT NULL,
                model_return DOUBLE PRECISION NOT NULL,
                sp500_return DOUBLE PRECISION NOT NULL,
                model_cumulative_return DOUBLE PRECISION NOT NULL,
                sp500_cumulative_return DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (model_name, return_date)
            )
            """
        )

        # Real daily returns of models traded on IBKR (written by
        # ibkr-trader's report phase). No FK to models: the whole-account
        # row ("ibkr_account") is not a model.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ibkr_returns (
                model_name TEXT NOT NULL,
                return_date DATE NOT NULL,
                real_return DOUBLE PRECISION NOT NULL,
                pnl DOUBLE PRECISION,
                capital DOUBLE PRECISION,
                PRIMARY KEY (model_name, return_date)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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


# --- Models / portfolios / stock prices / returns -----------------------
#
# Populated by import_data.py (run daily); the Flask API only ever reads
# these, it never touches the models_output_json / stocks_data.csv files
# directly.


def upsert_model(name: str, description: str | None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO models (name, description)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE
                SET description = COALESCE(EXCLUDED.description, models.description)
            """,
            (name, description),
        )


def get_all_model_names() -> list[str]:
    with get_cursor() as cur:
        cur.execute("SELECT name FROM models ORDER BY name")
        return [row["name"] for row in cur.fetchall()]


def model_exists(name: str) -> bool:
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM models WHERE name = %s", (name,))
        return cur.fetchone() is not None


def get_model_description(name: str) -> str | None:
    with get_cursor() as cur:
        cur.execute("SELECT description FROM models WHERE name = %s", (name,))
        row = cur.fetchone()
        return row["description"] if row else None


def get_existing_portfolio_filenames(model_name: str) -> set[str]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT filename FROM portfolios WHERE model_name = %s", (model_name,)
        )
        return {row["filename"] for row in cur.fetchall()}


def get_stored_portfolio_data(model_name: str) -> dict[str, Any]:
    """{filename: portfolio_data} for everything imported for a model."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT filename, portfolio_data FROM portfolios WHERE model_name = %s",
            (model_name,),
        )
        return {row["filename"]: row["portfolio_data"] for row in cur.fetchall()}


def update_portfolio_data(model_name: str, filename: str, portfolio_data: Any) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE portfolios SET portfolio_data = %s
            WHERE model_name = %s AND filename = %s
            """,
            (psycopg2.extras.Json(portfolio_data), model_name, filename),
        )


def delete_portfolios(model_name: str, filenames: list[str]) -> None:
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM portfolios WHERE model_name = %s AND filename = ANY(%s)",
            (model_name, filenames),
        )


def delete_model(name: str) -> None:
    """Removes a model with its portfolios and returns (ON DELETE CASCADE)."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM models WHERE name = %s", (name,))


def insert_portfolio(
    model_name: str, portfolio_date: date, filename: str, portfolio_data: Any
) -> None:
    """
    No-ops if (model_name, filename) already exists -- callers are expected
    to have already filtered with get_existing_portfolio_filenames(), this
    is just a second layer of protection against double-importing.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolios (model_name, portfolio_date, filename, portfolio_data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (model_name, filename) DO NOTHING
            """,
            (model_name, portfolio_date, filename, psycopg2.extras.Json(portfolio_data)),
        )


def get_portfolios_for_model(model_name: str) -> list[dict[str, Any]]:
    """Returns portfolios ordered ascending by portfolio_date, filename."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT portfolio_date, filename, portfolio_data,
                   COALESCE(returns_until_next_change, '{}'::jsonb) AS returns_until_next_change,
                   return_until_date, is_current_portfolio
            FROM portfolios
            WHERE model_name = %s
            ORDER BY portfolio_date, filename
            """,
            (model_name,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_latest_portfolio(model_name: str) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT portfolio_date, filename, portfolio_data
            FROM portfolios
            WHERE model_name = %s
            ORDER BY portfolio_date DESC, filename DESC
            LIMIT 1
            """,
            (model_name,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_portfolio_windows(model_name: str, windows: list[dict[str, Any]]) -> None:
    """
    windows: dicts with "filename", "returns_until_next_change",
    "return_until_date", "is_current_portfolio" -- see
    portfolio_calc.build_portfolio_return_windows().
    """
    with get_cursor() as cur:
        for window in windows:
            cur.execute(
                """
                UPDATE portfolios
                SET returns_until_next_change = %s,
                    return_until_date = %s,
                    is_current_portfolio = %s
                WHERE model_name = %s AND filename = %s
                """,
                (
                    psycopg2.extras.Json(window["returns_until_next_change"]),
                    window["return_until_date"],
                    window["is_current_portfolio"],
                    model_name,
                    window["filename"],
                ),
            )


def upsert_stock_prices(rows: list[tuple[str, date, float]]) -> int:
    """
    rows: (symbol, price_date, price). Inserts new prices and updates changed
    ones (Yahoo revises past closes, e.g. for dividend adjustments). Returns
    the number of rows inserted, changed or removed.
    """
    if not rows:
        return 0
    with get_cursor() as cur:
        # Prices that are no longer in the source file (dropped symbols,
        # blanked dates) go too, so every database matches the CSV exactly.
        cur.execute("CREATE TEMP TABLE incoming (symbol TEXT, price_date DATE) ON COMMIT DROP")
        psycopg2.extras.execute_values(
            cur, "INSERT INTO incoming VALUES %s", [(s, d) for s, d, _ in rows], page_size=5000
        )
        cur.execute(
            """
            DELETE FROM stock_prices p
            WHERE NOT EXISTS (
                SELECT 1 FROM incoming i
                WHERE i.symbol = p.symbol AND i.price_date = p.price_date
            )
            """
        )
        removed = cur.rowcount
        # fetch=True makes execute_values() collect the RETURNING output
        # across every page, so the count is accurate regardless of paging
        # (plain cur.rowcount would only reflect the last page).
        inserted = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO stock_prices (symbol, price_date, price)
            VALUES %s
            ON CONFLICT (symbol, price_date) DO UPDATE SET price = EXCLUDED.price
                WHERE stock_prices.price IS DISTINCT FROM EXCLUDED.price
            RETURNING 1
            """,
            rows,
            page_size=5000,
            fetch=True,
        )
        return len(inserted) + removed


def get_setting(key: str) -> str | None:
    with get_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (key, value),
        )


def load_stock_data_frame() -> pd.DataFrame:
    """
    Returns the full stock_prices table pivoted into the same shape the API
    used to load from stocks_data.csv: rows indexed by date, one column per
    symbol.
    """
    with get_cursor() as cur:
        cur.execute("SELECT symbol, price_date, price FROM stock_prices")
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["price_date"] = pd.to_datetime(df["price_date"])
    return df.pivot(index="price_date", columns="symbol", values="price").sort_index()


def get_stock_price_series(symbol: str) -> pd.Series:
    with get_cursor() as cur:
        cur.execute(
            "SELECT price_date, price FROM stock_prices "
            "WHERE symbol = %s ORDER BY price_date",
            (symbol,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    index = pd.to_datetime([row["price_date"] for row in rows])
    return pd.Series([row["price"] for row in rows], index=index)


def replace_model_returns(model_name: str, returns_data: list[dict[str, Any]]) -> None:
    """
    Replaces the model's daily returns with returns_data (as produced by
    portfolio_calc.calculate_model_returns()), so dates that are no longer
    produced (e.g. a model whose history was regenerated) do not linger.
    """
    if not returns_data:
        return
    rows = [
        (
            model_name,
            entry["date"],
            entry["model_return"],
            entry["sp500_return"],
            entry["model_cumulative_return"],
            entry["sp500_cumulative_return"],
        )
        for entry in returns_data
    ]
    with get_cursor() as cur:
        # Same transaction, so readers never see the model without returns.
        cur.execute("DELETE FROM model_returns WHERE model_name = %s", (model_name,))
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO model_returns
                (model_name, return_date, model_return, sp500_return,
                 model_cumulative_return, sp500_cumulative_return)
            VALUES %s
            ON CONFLICT (model_name, return_date) DO UPDATE SET
                model_return = EXCLUDED.model_return,
                sp500_return = EXCLUDED.sp500_return,
                model_cumulative_return = EXCLUDED.model_cumulative_return,
                sp500_cumulative_return = EXCLUDED.sp500_cumulative_return
            """,
            rows,
        )


def replace_ibkr_returns(rows: list[tuple[str, date, float, float | None, float | None]]) -> None:
    """
    rows: (model_name, return_date, real_return, pnl, capital). Replaces the
    whole table, since the source CSV is the complete history.
    """
    with get_cursor() as cur:
        cur.execute("DELETE FROM ibkr_returns")
        if rows:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO ibkr_returns
                    (model_name, return_date, real_return, pnl, capital)
                VALUES %s
                ON CONFLICT (model_name, return_date) DO UPDATE SET
                    real_return = EXCLUDED.real_return,
                    pnl = EXCLUDED.pnl,
                    capital = EXCLUDED.capital
                """,
                rows,
            )


def get_ibkr_model_names() -> list[str]:
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT model_name FROM ibkr_returns ORDER BY model_name")
        return [row["model_name"] for row in cur.fetchall()]


def get_ibkr_returns(model_name: str) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT return_date, real_return, pnl, capital
            FROM ibkr_returns
            WHERE model_name = %s
            ORDER BY return_date
            """,
            (model_name,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_model_returns(model_name: str) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT return_date, model_return, sp500_return,
                   model_cumulative_return, sp500_cumulative_return
            FROM model_returns
            WHERE model_name = %s
            ORDER BY return_date
            """,
            (model_name,),
        )
        return [dict(row) for row in cur.fetchall()]
