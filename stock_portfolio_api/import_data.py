"""
Daily import job: loads portfolio JSON files and stocks_data.csv into
Postgres (skipping anything already imported), then (re)computes each
model's returns_until_next_change window and its daily returns vs S&P 500.

The Flask API (app.py) never touches models_output_json / stocks_data.csv
-- it only reads what this script writes. Run once a day, after the
day's portfolio files and price data have landed on disk, e.g.:

    docker compose exec -T stock_portfolio_api python import_data.py
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import db
import portfolio_calc as calc

BASE_DIR = Path("/app")
MODELS_OUTPUT_DIR = Path(
    os.getenv("MODELS_OUTPUT_DIR", BASE_DIR / "models_output_json")
).resolve()
STOCKS_DATA_PATH = Path(
    os.getenv("STOCKS_DATA_PATH", BASE_DIR / "stocks_data.csv")
).resolve()

IBKR_RETURNS_PATH = Path(
    os.getenv("IBKR_RETURNS_PATH", BASE_DIR / "data" / "ibkr" / "real_returns.csv")
).resolve()

SP500_SYMBOL_SETTING = "sp500_symbol"

# Matches filenames such as:
# portfolio_20260615.json
# model_result_v2_20260615.json
PORTFOLIO_FILE_PATTERN = re.compile(
    r"^.+_(?P<date>\d{8})\.json$",
    re.IGNORECASE,
)


def find_portfolio_files(model_dir: Path) -> list[tuple[datetime, Path]]:
    """
    Return valid portfolio files sorted by the YYYYMMDD date in the filename.
    Files with malformed names or impossible dates are ignored.
    """
    results: list[tuple[datetime, Path]] = []

    for path in model_dir.iterdir():
        if not path.is_file():
            continue

        match = PORTFOLIO_FILE_PATTERN.fullmatch(path.name)
        if not match:
            continue

        try:
            portfolio_date = datetime.strptime(match.group("date"), "%Y%m%d")
        except ValueError:
            continue

        results.append((portfolio_date, path))

    results.sort(key=lambda item: (item[0], item[1].name))
    return results


def import_models_and_portfolios() -> None:
    if not MODELS_OUTPUT_DIR.is_dir():
        print(f"models directory not found at {MODELS_OUTPUT_DIR}, skipping")
        return

    model_dirs = sorted(
        entry for entry in MODELS_OUTPUT_DIR.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )

    for model_dir in model_dirs:
        model_name = model_dir.name
        desc_path = model_dir / "desc.txt"
        description = (
            desc_path.read_text(encoding="utf-8").strip()
            if desc_path.is_file()
            else None
        )
        db.upsert_model(model_name, description or None)

        files = find_portfolio_files(model_dir)
        already_imported = db.get_existing_portfolio_filenames(model_name)
        new_files = [
            (portfolio_date, path)
            for portfolio_date, path in files
            if path.name not in already_imported
        ]

        for portfolio_date, path in new_files:
            try:
                portfolio_json = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"  skipping invalid JSON in {path.name}: {exc}")
                continue
            db.insert_portfolio(model_name, portfolio_date.date(), path.name, portfolio_json)

        if new_files:
            print(f"{model_name}: imported {len(new_files)} new portfolio file(s)")


def import_stock_prices() -> None:
    if not STOCKS_DATA_PATH.is_file():
        print(f"stock data file not found at {STOCKS_DATA_PATH}, skipping")
        return

    stock_data = pd.read_csv(STOCKS_DATA_PATH)
    date_col = stock_data.columns[0]
    sp500_symbol = stock_data.columns[-1]

    stock_data[date_col] = pd.to_datetime(stock_data[date_col])

    # The CSV has an unnamed, always-empty column right after the date
    # column (an export artifact); drop any such columns rather than
    # importing them as bogus symbols.
    value_cols = [
        col for col in stock_data.columns[1:]
        if not str(col).startswith("Unnamed")
    ]

    long_df = stock_data.melt(
        id_vars=[date_col], value_vars=value_cols, var_name="symbol", value_name="price"
    ).dropna(subset=["price"])

    rows = [
        (str(symbol), price_date.date(), float(price))
        for price_date, symbol, price in long_df.itertuples(index=False, name=None)
    ]

    inserted = db.insert_stock_prices_if_missing(rows)
    db.set_setting(SP500_SYMBOL_SETTING, sp500_symbol)
    print(
        f"stock prices: {inserted} new row(s) inserted "
        f"({len(rows)} candidate rows checked), sp500 symbol={sp500_symbol!r}"
    )


def recompute_model_returns() -> None:
    stock_data = db.load_stock_data_frame()
    if stock_data.empty:
        print("no stock price data in database, skipping returns computation")
        return

    sp500_symbol = db.get_setting(SP500_SYMBOL_SETTING)
    sp500_prices = (
        stock_data[sp500_symbol] if sp500_symbol in stock_data.columns else None
    )
    if sp500_prices is None:
        print(
            f"sp500 symbol {sp500_symbol!r} not found in stock price data, "
            "skipping returns computation"
        )
        return

    for model_name in db.get_all_model_names():
        portfolios = db.get_portfolios_for_model(model_name)
        if not portfolios:
            continue

        for entry in portfolios:
            entry["portfolio_date"] = pd.Timestamp(entry["portfolio_date"])
            entry["holdings"] = calc.parse_portfolio_data(entry["portfolio_data"])

        windows = calc.build_portfolio_return_windows(stock_data, portfolios)
        db.update_portfolio_windows(model_name, windows)

        returns_data = calc.calculate_model_returns(stock_data, sp500_prices, portfolios)
        db.replace_model_returns(model_name, returns_data)

        print(
            f"{model_name}: updated {len(windows)} portfolio window(s), "
            f"{len(returns_data)} daily return row(s)"
        )


def import_ibkr_returns() -> None:
    """
    Loads the real daily returns ibkr-trader records for the models it
    trades on the IBKR paper account (columns: date, model, real_return,
    pnl, capital, ...). The file is the full history, so the table is
    replaced on every run.
    """
    if not IBKR_RETURNS_PATH.is_file():
        print(f"ibkr returns file not found at {IBKR_RETURNS_PATH}, skipping")
        return

    df = pd.read_csv(IBKR_RETURNS_PATH)
    df = df.dropna(subset=["date", "model", "real_return"])
    # A re-run report for the same day overwrites the earlier row.
    df = df.drop_duplicates(subset=["model", "date"], keep="last")

    def optional(value):
        return None if pd.isna(value) else float(value)

    rows = [
        (
            str(row.model),
            pd.Timestamp(row.date).date(),
            float(row.real_return),
            optional(getattr(row, "pnl", None)),
            optional(getattr(row, "capital", None)),
        )
        for row in df.itertuples(index=False)
    ]
    db.replace_ibkr_returns(rows)
    print(f"ibkr returns: {len(rows)} row(s) for {df['model'].nunique()} model(s)")


def main() -> None:
    db.init_db()
    import_models_and_portfolios()
    import_stock_prices()
    recompute_model_returns()
    import_ibkr_returns()


if __name__ == "__main__":
    main()
