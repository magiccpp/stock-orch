from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import pandas as pd
from flask import Flask, jsonify, request
from flask_compress import Compress
from flasgger import Swagger

import db
from auth import register_auth_routes
from errors import ApiError, error_response
from utils import client_ip


BASE_DIR = Path('/app')
MODELS_OUTPUT_DIR = Path(
    os.getenv("MODELS_OUTPUT_DIR", BASE_DIR / "models_output_json")
).resolve()
STOCKS_DATA_PATH = Path(
    os.getenv("STOCKS_DATA_PATH", BASE_DIR / "stocks_data.csv")
).resolve()
VISITS_DATA_PATH = Path(
    os.getenv("VISITS_DATA_PATH", BASE_DIR / "data" / "visits.json")
).resolve()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable must be set")

_visits_lock = threading.Lock()

_STOCKS_DATA_CACHE: pd.DataFrame | None = None
_STOCKS_DATA_CACHE_MTIME_NS: int | None = None

# Per-model cache of computed portfolio entries (including the
# returns_until_next_change calculation, which is expensive since it walks
# every held stock for every historical portfolio). Keyed by model_name,
# valued as (cache_key, portfolios).
_PORTFOLIO_RETURNS_CACHE: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
_PORTFOLIO_RETURNS_CACHE_LOCK = threading.Lock()

# Matches filenames such as:
# portfolio_20260615.json
# model_result_v2_20260615.json
PORTFOLIO_FILE_PATTERN = re.compile(
    r"^.+_(?P<date>\d{8})\.json$",
    re.IGNORECASE,
)


def create_app() -> Flask:
    app = Flask(__name__)

    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    app.config["JSON_SORT_KEYS"] = False
    # Compress larger responses such as historical portfolio payloads.
    app.config["COMPRESS_MIMETYPES"] = [
        "application/json",
        "application/javascript",
        "text/css",
        "text/html",
        "text/xml",
        "text/plain",
        "image/svg+xml",
    ]
    app.config["COMPRESS_LEVEL"] = int(os.getenv("COMPRESS_LEVEL", "6"))
    app.config["COMPRESS_MIN_SIZE"] = int(
        os.getenv("COMPRESS_MIN_SIZE", "1024")
    )
    app.config["COMPRESS_ALGORITHM"] = os.getenv(
        "COMPRESS_ALGORITHM", "gzip"
    )

    Compress(app)

    app.config["SWAGGER"] = {
        "title": "Stock AI Portfolio API",
        "uiversion": 3,
        "openapi": "3.0.3",
        "specs_route": "/apidocs/",
    }

    Swagger(
        app,
        template={
            "info": {
                "title": "Stock AI Portfolio API",
                "description": (
                    "Read stock-AI model portfolios from the "
                    "models_output_json directory."
                ),
                "version": "1.0.0",
            },
            "servers": [{"url": "/"}],
        },
    )

    db.init_db()
    register_routes(app)
    register_auth_routes(app)
    register_error_handlers(app)
    return app


def calculate_model_returns(stock_data: pd.DataFrame, sp500_prices: pd.Series, portfolio_files: list[tuple[datetime, Path]]) -> list[dict]:
    """
    Calculate daily returns for the model portfolio and S&P 500 starting from the first portfolio date.
    """
    print("calculate_model_returns")
    if not portfolio_files:
        return []
    
    returns_data = []
    current_portfolio = {}
    portfolio_idx = 0
    
    # Get the date of the first portfolio
    first_portfolio_date = portfolio_files[0][0]
    
    # Find all trading dates from the first portfolio date onwards
    available_dates = stock_data.index[stock_data.index >= first_portfolio_date]
    
    if len(available_dates) == 0:
        return []  # No stock data available from the portfolio start date

    # Precompute per-stock returns using each stock's own consecutive valid trading dates.
    # dropna() ensures pct_change() steps over NaN rows (e.g. different market holidays).
    stock_returns_map: dict[str, pd.Series] = {
        col: stock_data[col].dropna().pct_change()
        for col in stock_data.columns
    }
    sp500_pct = sp500_prices.dropna().pct_change()

    # Initialize cumulative returns
    model_cumulative = 1.0
    sp500_cumulative = 1.0

    for i, date in enumerate(available_dates):
        # Load new portfolio if available for this date or before
        while (portfolio_idx < len(portfolio_files) and
               portfolio_files[portfolio_idx][0] < date):
            portfolio_data = read_json_file(portfolio_files[portfolio_idx][1])
            current_portfolio = parse_portfolio_data(portfolio_data)
            portfolio_idx += 1

        # Default first-day returns to zero since there is no previous day.
        model_return = 0.0
        sp500_return = 0.0

        if i > 0:  # Skip first day since we need previous day for return calculation
            # Calculate model return
            if current_portfolio:
                # Only include stocks that traded on this specific date.
                stocks = [s for s in current_portfolio
                          if s in stock_returns_map and date in stock_returns_map[s].index]
                if stocks:
                    weights = pd.Series({s: current_portfolio[s] for s in stocks})
                    returns = pd.Series({s: stock_returns_map[s].loc[date] for s in stocks})
                    valid = returns.notna()
                    if valid.any():
                        model_return = float(weights[valid].dot(returns[valid]))
                print(f"Model return for date {date}: {model_return}")
            # Calculate S&P 500 return
            if date in sp500_pct.index and pd.notna(sp500_pct.loc[date]):
                sp500_return = float(sp500_pct.loc[date])
            
            # Update cumulative returns
            model_cumulative *= (1 + model_return)
            sp500_cumulative *= (1 + sp500_return)
        
        returns_data.append({
            "date": date.strftime("%Y-%m-%d"),
            "model_return": round(model_return, 6),
            "sp500_return": round(sp500_return, 6),
            "model_cumulative_return": round(model_cumulative - 1, 6),
            "sp500_cumulative_return": round(sp500_cumulative - 1, 6),
        })
    
    return returns_data


def calculate_stock_return(
    price_series: pd.Series, start_date: datetime, end_date: datetime
) -> float | None:
    """
    Return the fractional price change of a single stock from the last
    trading close before start_date through the last trading close on/before
    end_date.

    The baseline is anchored to the close *before* start_date (rather than
    start_date's own close) so that start_date's own move is included in the
    result. This matters because portfolio filenames are dated in Beijing
    time while stock_data is indexed by New York trading dates: a portfolio
    dated e.g. 2026-07-17 is typically already decided using the 07-17 NY
    close and held from that day forward, so 07-17's own return belongs to
    it -- not just returns from 07-18 onwards. Without this, a portfolio
    replaced the very next calendar day would span zero trading dates and
    report no return at all.

    Falls back to start_date's own close as the baseline only when there is
    no earlier trading data at all (e.g. start_date is the first date in the
    series).

    Returns None if there isn't enough data to compute a return.
    """
    valid = price_series.dropna()
    before_start = valid.index[valid.index < start_date]
    end_candidates = valid.index[valid.index <= end_date]

    if len(end_candidates) == 0:
        return None

    if len(before_start) > 0:
        start_date_actual = before_start[-1]
    else:
        on_or_after_start = valid.index[valid.index >= start_date]
        if len(on_or_after_start) == 0:
            return None
        start_date_actual = on_or_after_start[0]

    end_date_actual = end_candidates[-1]

    if end_date_actual <= start_date_actual:
        return None

    start_price = valid.loc[start_date_actual]
    if start_price == 0:
        return None

    end_price = valid.loc[end_date_actual]
    return float(end_price / start_price - 1)


def calculate_range_return(
    price_series: pd.Series, start_date: datetime, end_date: datetime
) -> float | None:
    """
    Return the fractional price change of a single stock between the first
    available trading date on/after start_date and the last available
    trading date on/before end_date -- a plain, literal date-range return.

    Unlike calculate_stock_return(), this does NOT anchor to the close
    before start_date. That anchoring exists specifically to compensate for
    portfolio filenames being timezone-shifted relative to stock_data; here
    start_date/end_date come directly from the performance chart's own
    trading-date axis (a user's drag-selection on the chart), so they are
    already aligned to stock_data's index and no such shift is needed.

    Returns None if there isn't enough data to compute a return.
    """
    valid = price_series.dropna()
    start_candidates = valid.index[valid.index >= start_date]
    end_candidates = valid.index[valid.index <= end_date]

    if len(start_candidates) == 0 or len(end_candidates) == 0:
        return None

    start_date_actual = start_candidates[0]
    end_date_actual = end_candidates[-1]

    if end_date_actual <= start_date_actual:
        return None

    start_price = valid.loc[start_date_actual]
    if start_price == 0:
        return None

    end_price = valid.loc[end_date_actual]
    return float(end_price / start_price - 1)


def calculate_returns_until_next_change(
    stock_data: pd.DataFrame,
    holdings: dict[str, float],
    portfolio_date: datetime,
    end_date: datetime,
) -> dict[str, float]:
    """
    For a single portfolio, compute each held stock's return from
    portfolio_date until end_date (the date of the next portfolio, or the
    latest available trading date if this is the current portfolio).
    """
    returns = {}
    for symbol in holdings:
        if symbol not in stock_data.columns:
            continue
        stock_return = calculate_stock_return(
            stock_data[symbol], portfolio_date, end_date
        )
        if stock_return is not None:
            returns[symbol] = round(stock_return, 6)
    return returns


def _build_portfolio_entries(
    files: list[tuple[datetime, Path]], stock_data: pd.DataFrame | None
) -> list[dict[str, Any]]:
    """
    Read every portfolio file for a model and, if stock_data is available,
    compute each one's returns_until_next_change. This walks every held
    stock for every historical portfolio, so it's the expensive part of
    serving portfolio history -- callers should go through
    get_portfolio_entries_cached() rather than calling this on every
    request.
    """
    last_available_date = (
        stock_data.index.max() if stock_data is not None else None
    )

    portfolios = []
    for i, (portfolio_date, path) in enumerate(files):
        portfolio_json = read_json_file(path)
        is_current = i + 1 >= len(files)
        end_date = last_available_date if is_current else files[i + 1][0]

        returns_until_next_change = {}
        if stock_data is not None and end_date is not None:
            holdings = parse_portfolio_data(portfolio_json)
            returns_until_next_change = calculate_returns_until_next_change(
                stock_data, holdings, portfolio_date, end_date
            )

        portfolios.append(
            {
                "portfolio_date": portfolio_date.strftime("%Y-%m-%d"),
                "filename": path.name,
                "portfolio": portfolio_json,
                "returns_until_next_change": returns_until_next_change,
                "return_until_date": (
                    end_date.strftime("%Y-%m-%d")
                    if end_date is not None
                    else None
                ),
                "is_current_portfolio": is_current,
            }
        )

    return portfolios


def get_portfolio_entries_cached(
    model_name: str,
    files: list[tuple[datetime, Path]],
    stock_data: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """
    Cached wrapper around _build_portfolio_entries(). The per-stock return
    calculations are recomputed at most once per calendar day, and sooner
    if this model's portfolio files or the underlying stock price data have
    changed since the last computation (so newly published portfolios still
    show up immediately instead of waiting for the daily boundary).
    """
    files_signature = tuple(
        (path.name, path.stat().st_mtime_ns) for _, path in files
    )
    stock_data_signature = (
        _STOCKS_DATA_CACHE_MTIME_NS if stock_data is not None else None
    )
    cache_key = (datetime.now().date(), files_signature, stock_data_signature)

    with _PORTFOLIO_RETURNS_CACHE_LOCK:
        cached = _PORTFOLIO_RETURNS_CACHE.get(model_name)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

    portfolios = _build_portfolio_entries(files, stock_data)

    with _PORTFOLIO_RETURNS_CACHE_LOCK:
        _PORTFOLIO_RETURNS_CACHE[model_name] = (cache_key, portfolios)

    return portfolios


def load_stocks_data() -> pd.DataFrame:
    global _STOCKS_DATA_CACHE
    global _STOCKS_DATA_CACHE_MTIME_NS

    if not STOCKS_DATA_PATH.exists():
        raise ApiError(
            404,
            "Stock data not found",
            f"Stock data file not found at: {STOCKS_DATA_PATH}",
        )

    current_mtime_ns = STOCKS_DATA_PATH.stat().st_mtime_ns
    if (
        _STOCKS_DATA_CACHE is not None
        and _STOCKS_DATA_CACHE_MTIME_NS == current_mtime_ns
    ):
        print("Using cached stock data")
        return _STOCKS_DATA_CACHE
    
    print(f"Loading stock data from {STOCKS_DATA_PATH}")

    stock_data = pd.read_csv(STOCKS_DATA_PATH)

    # Ensure the first column is treated as date and convert to datetime.
    stock_data.iloc[:, 0] = pd.to_datetime(stock_data.iloc[:, 0])
    stock_data = stock_data.set_index(stock_data.columns[0]).sort_index()

    _STOCKS_DATA_CACHE = stock_data
    _STOCKS_DATA_CACHE_MTIME_NS = current_mtime_ns
    return stock_data


def parse_portfolio_data(portfolio_data: Any) -> dict[str, float]:
    """
    Parse portfolio data into a standardized dict format {stock: weight}.
    Handles various portfolio data formats.
    """
    if isinstance(portfolio_data, dict):
        # If it's already a dict, assume it's {stock: weight}
        return {str(k): float(v) for k, v in portfolio_data.items() if isinstance(v, (int, float))}
    
    elif isinstance(portfolio_data, list):
        # Handle list format - try to detect the structure
        if not portfolio_data:
            return {}
        
        first_item = portfolio_data[0]
        
        if isinstance(first_item, dict):
            # List of dictionaries - common formats:
            # [{"id": "AAPL", "weight": 0.1}, ...]  <- Your format
            # [{"symbol": "AAPL", "weight": 0.1}, ...]
            # [{"stock": "AAPL", "allocation": 0.1}, ...]
            portfolio = {}
            for item in portfolio_data:
                if isinstance(item, dict):
                    # Try common field names for stock symbol
                    symbol = None
                    weight = None
                    
                    # Look for stock symbol - prioritize "id" since that's your format
                    for key in ["id", "symbol", "stock", "ticker", "name", "asset"]:
                        if key in item:
                            symbol = str(item[key])
                            break
                    
                    # Look for weight
                    for key in ["weight", "allocation", "position", "value", "percent", "ratio"]:
                        if key in item and isinstance(item[key], (int, float)):
                            weight = float(item[key])
                            break
                    
                    if symbol and weight is not None:
                        portfolio[symbol] = weight
            
            return portfolio
        
        elif isinstance(first_item, (list, tuple)) and len(first_item) == 2:
            # List of pairs: [["AAPL", 0.1], ["GOOGL", 0.2], ...]
            portfolio = {}
            for item in portfolio_data:
                if len(item) == 2:
                    symbol, weight = item
                    if isinstance(weight, (int, float)):
                        portfolio[str(symbol)] = float(weight)
            return portfolio
        
        else:
            # List of strings or other format - assume equal weights
            symbols = [str(item) for item in portfolio_data if isinstance(item, str)]
            if symbols:
                equal_weight = 1.0 / len(symbols)
                return {symbol: equal_weight for symbol in symbols}
    
    # If we can't parse it, return empty portfolio
    return {}


def register_routes(app: Flask) -> None:
    @app.get("/api/models")
    def get_models():
        """
        Fetch all stock AI models.
        ---
        tags:
          - Models
        summary: List all available stock AI models
        description: >
          Returns the names of all direct subdirectories under
          models_output_json.
        responses:
          200:
            description: Model list returned successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    count:
                      type: integer
                      example: 2
                    models:
                      type: array
                      items:
                        type: string
                      example:
                        - momentum_model
                        - value_model
          500:
            description: The configured model directory is unavailable
        """
        ensure_output_directory()
        models = sorted(
            entry.name
            for entry in MODELS_OUTPUT_DIR.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
        return jsonify({"count": len(models), "models": models})

    @app.get("/api/models/<string:model_name>/portfolio/latest")
    def get_latest_portfolio(model_name: str):
        """
        Get the latest portfolio produced by a model.
        ---
        tags:
          - Portfolios
        summary: Get a model's latest portfolio
        parameters:
          - in: path
            name: model_name
            required: true
            schema:
              type: string
            description: Exact subdirectory name under models_output_json
            example: momentum_model
        responses:
          200:
            description: Latest portfolio returned successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    model_name:
                      type: string
                    portfolio_date:
                      type: string
                      format: date
                    filename:
                      type: string
                    portfolio:
                      description: JSON content loaded from the portfolio file
          400:
            description: Invalid model name
          404:
            description: Model or matching portfolio file not found
          422:
            description: The latest portfolio file contains invalid JSON
        """
        model_dir = resolve_model_directory(model_name)
        files = find_portfolio_files(model_dir)

        if not files:
            return error_response(
                404,
                "No portfolio files found",
                f"No files matching '*_YYYYMMDD.json' were found for model "
                f"'{model_name}'.",
            )

        portfolio_date, latest_path = files[-1]
        portfolio = read_json_file(latest_path)

        return jsonify(
            {
                "model_name": model_name,
                "portfolio_date": portfolio_date.strftime("%Y-%m-%d"),
                "filename": latest_path.name,
                "portfolio": portfolio,
            }
        )
    
    @app.get("/api/models/<string:model_name>/returns")
    def get_returns(model_name: str):
        """
        Get historical returns for a model compared to S&P 500.
        ---
        tags:
          - Returns
        summary: Get model's historical returns vs S&P 500
        parameters:
          - in: path
            name: model_name
            required: true
            schema:
              type: string
            description: Exact subdirectory name under models_output_json
            example: momentum_model
        responses:
                    200:
            description: Returns data returned successfully
            content:
              application/json:
                                schema:
                  type: object
                  properties:
                    model_name:
                                            type: string
                    returns:
                      type: array
                      items:
                        type: object
                        properties:
                          date:
                            type: string
                                                        format: date
                          model_return:
                            type: number
                          sp500_return:
                            type: number
                          model_cumulative_return:
                                                        type: number
                          sp500_cumulative_return:
                            type: number
          400:
            description: Invalid model name
                    404:
            description: Model not found or no stock data available
          500:
            description: Error reading stock data or calculating returns
        """
        try:
            # Validate model exists
            model_dir = resolve_model_directory(model_name)
            stock_data = load_stocks_data()

            # Get S&P 500 data (last column)
            sp500_prices = stock_data.iloc[:, -1]
            
            # Get model's historical portfolios
            portfolio_files = find_portfolio_files(model_dir)
            
            if not portfolio_files:
                return error_response(
                    404,
                    "No portfolio files found",
                    f"No portfolio files found for model '{model_name}'.",
                )
            
            # Calculate returns
            returns_data = calculate_model_returns(
                stock_data, sp500_prices, portfolio_files
            )
            
            return jsonify({
                "model_name": model_name,
                "returns": returns_data
            })
            
        except Exception as exc:
            return error_response(
                500,
                "Error calculating returns",
                f"An error occurred while calculating returns: {str(exc)}",
            )



    @app.get("/api/models/<string:model_name>/portfolios")
    def get_historical_portfolios(model_name: str):
        """
        Get all historical portfolios for a model.
        ---
        tags:
          - Portfolios
        summary: Get all historical portfolios for a model
        parameters:
          - in: path
            name: model_name
            required: true
            schema:
              type: string
            description: Exact subdirectory name under models_output_json
            example: momentum_model
        responses:
          200:
            description: Historical portfolios returned successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    model_name:
                      type: string
                    count:
                      type: integer
                    portfolios:
                      type: array
                      items:
                        type: object
                        properties:
                          portfolio_date:
                            type: string
                            format: date
                          filename:
                            type: string
                          portfolio:
                            description: JSON content from one source file
                          returns_until_next_change:
                            description: >
                              Map of {stock: return} earned by each held
                              stock between this portfolio's date and the
                              next portfolio's date (or the latest available
                              trading date for the current portfolio).
                            type: object
                          return_until_date:
                            type: string
                            format: date
                            nullable: true
                          is_current_portfolio:
                            type: boolean
          400:
            description: Invalid model name
          404:
            description: Model or matching portfolio files not found
          422:
            description: A portfolio file contains invalid JSON
        """
        model_dir = resolve_model_directory(model_name)
        files = find_portfolio_files(model_dir)

        if not files:
            return error_response(
                404,
                "No portfolio files found",
                f"No files matching '*_YYYYMMDD.json' were found for model "
                f"'{model_name}'.",
            )

        try:
            stock_data = load_stocks_data()
        except ApiError:
            stock_data = None

        portfolios = get_portfolio_entries_cached(model_name, files, stock_data)

        return jsonify(
            {
                "model_name": model_name,
                "count": len(portfolios),
                "portfolios": portfolios,
            }
        )

    @app.get("/api/stocks/returns")
    def get_stock_returns_in_range():
        """
        Get each requested stock's price return over an arbitrary date range.
        ---
        tags:
          - Returns
        summary: Get returns for a set of stocks between two dates
        description: >
          Used by the "drag to select a range" feature on the performance
          chart: given the stocks held by any portfolio touched by that
          range and the range's two endpoints, returns each stock's price
          change between them.
        parameters:
          - in: query
            name: symbols
            required: true
            schema:
              type: string
            description: Comma-separated stock symbols matching stocks_data.csv columns
            example: AAPL,GOOG
          - in: query
            name: start
            required: true
            schema:
              type: string
              format: date
          - in: query
            name: end
            required: true
            schema:
              type: string
              format: date
        responses:
          200:
            description: Returns computed successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    start_date:
                      type: string
                      format: date
                    end_date:
                      type: string
                      format: date
                    returns:
                      description: Map of {stock: return}; symbols with insufficient data are omitted
                      type: object
          400:
            description: Missing or invalid symbols/start/end parameters
          404:
            description: Stock data not found
        """
        symbols = [
            symbol.strip()
            for symbol in request.args.get("symbols", "").split(",")
            if symbol.strip()
        ]
        if not symbols:
            return error_response(
                400,
                "Missing symbols",
                "Query parameter 'symbols' must contain at least one stock symbol.",
            )

        try:
            start_date = datetime.strptime(request.args.get("start", ""), "%Y-%m-%d")
            end_date = datetime.strptime(request.args.get("end", ""), "%Y-%m-%d")
        except ValueError:
            return error_response(
                400,
                "Invalid date",
                "Query parameters 'start' and 'end' must be YYYY-MM-DD dates.",
            )

        if end_date < start_date:
            return error_response(
                400,
                "Invalid date range",
                "'end' must be on or after 'start'.",
            )

        stock_data = load_stocks_data()

        returns = {}
        for symbol in symbols:
            if symbol not in stock_data.columns:
                continue
            stock_return = calculate_range_return(
                stock_data[symbol], start_date, end_date
            )
            if stock_return is not None:
                returns[symbol] = round(stock_return, 6)

        return jsonify(
            {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "returns": returns,
            }
        )

    @app.get("/api/models/<string:model_name>/description")
    def get_model_description(model_name: str):
        """
        Get the description of a model.
        ---
        tags:
          - Models
        summary: Get a model's description
        parameters:
          - in: path
            name: model_name
            required: true
            schema:
              type: string
            description: Exact subdirectory name under models_output_json
            example: momentum_model
        responses:
          200:
            description: Description returned successfully (null if none exists)
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    model_name:
                      type: string
                    description:
                      type: string
                      nullable: true
          400:
            description: Invalid model name
          404:
            description: Model not found
        """
        model_dir = resolve_model_directory(model_name)
        desc_path = model_dir / "desc.txt"

        description = None
        if desc_path.is_file():
            try:
                description = desc_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                return error_response(
                    500,
                    "Could not read description file",
                    f"Could not read '{desc_path.name}': {exc}",
                )

        return jsonify(
            {
                "model_name": model_name,
                "description": description or None,
            }
        )

    @app.post("/api/visits")
    def record_visit():
        """
        Record a page visit and return updated visit counters.
        ---
        tags:
          - Visits
        summary: Record a visit and return total/unique counts
        responses:
          200:
            description: Visit recorded successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    total:
                      type: integer
                    unique_visitors:
                      type: integer
        """
        ip = client_ip()
        with _visits_lock:
            data = _load_visits()
            data["total"] += 1
            if ip not in data["unique_ips"]:
                data["unique_ips"].append(ip)
            _save_visits(data)
            total = data["total"]
            unique_visitors = len(data["unique_ips"])

        return jsonify({"total": total, "unique_visitors": unique_visitors})

    @app.get("/api/visits")
    def get_visits():
        """
        Get current visit counters without recording a new visit.
        ---
        tags:
          - Visits
        summary: Get visit counters
        responses:
          200:
            description: Visit counters returned successfully
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    total:
                      type: integer
                    unique_visitors:
                      type: integer
        """
        with _visits_lock:
            data = _load_visits()

        return jsonify(
            {"total": data["total"], "unique_visitors": len(data["unique_ips"])}
        )

    @app.get("/health")
    def health():
        """
        Service health check.
        ---
        tags:
          - Service
        summary: Check whether the API process is running
        responses:
          200:
            description: Service is running
        """
        return jsonify(
            {
                "status": "ok",
                "models_output_directory": str(MODELS_OUTPUT_DIR),
                "directory_exists": MODELS_OUTPUT_DIR.is_dir(),
            }
        )


def _load_visits() -> dict:
    if not VISITS_DATA_PATH.is_file():
        return {"total": 0, "unique_ips": []}
    try:
        with VISITS_DATA_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"total": 0, "unique_ips": []}
    data.setdefault("total", 0)
    data.setdefault("unique_ips", [])
    return data


def _save_visits(data: dict) -> None:
    VISITS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = VISITS_DATA_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file)
    tmp_path.replace(VISITS_DATA_PATH)


def ensure_output_directory() -> None:
    if not MODELS_OUTPUT_DIR.exists():
        raise ApiError(
            500,
            "Model output directory not found",
            f"The configured directory does not exist: {MODELS_OUTPUT_DIR}",
        )
    if not MODELS_OUTPUT_DIR.is_dir():
        raise ApiError(
            500,
            "Invalid model output path",
            f"The configured path is not a directory: {MODELS_OUTPUT_DIR}",
        )


def resolve_model_directory(model_name: str) -> Path:
    """
    Resolve model_name safely and prevent paths such as '../secret'.
    """
    ensure_output_directory()

    if (
        not model_name
        or model_name in {".", ".."}
        or "/" in model_name
        or "\\" in model_name
        or "\x00" in model_name
    ):
        raise ApiError(
            400,
            "Invalid model name",
            "model_name must be a direct subdirectory name.",
        )

    model_dir = (MODELS_OUTPUT_DIR / model_name).resolve()

    try:
        model_dir.relative_to(MODELS_OUTPUT_DIR)
    except ValueError as exc:
        raise ApiError(
            400,
            "Invalid model name",
            "The requested model path is outside the model output directory.",
        ) from exc

    if not model_dir.is_dir():
        raise ApiError(
            404,
            "Model not found",
            f"Model '{model_name}' does not exist.",
        )

    return model_dir


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

    # The filename is a deterministic tie-breaker if two files have the same date.
    results.sort(key=lambda item: (item[0], item[1].name))
    return results


def read_json_file(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as exc:
        raise ApiError(
            422,
            "Invalid portfolio JSON",
            f"Could not parse '{path.name}': line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}",
        ) from exc
    except OSError as exc:
        raise ApiError(
            500,
            "Could not read portfolio file",
            f"Could not read '{path.name}': {exc}",
        ) from exc


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return error_response(exc.status_code, exc.error, exc.message)

    @app.errorhandler(404)
    def handle_route_not_found(_exc):
        return error_response(
            404,
            "Route not found",
            "The requested API route does not exist.",
        )

    @app.errorhandler(500)
    def handle_internal_error(_exc):
        return error_response(
            500,
            "Internal server error",
            "An unexpected server error occurred.",
        )


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
