from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
import pandas as pd
from flask import Flask, jsonify
from flask_compress import Compress
from flasgger import Swagger


BASE_DIR = Path('/app')
MODELS_OUTPUT_DIR = Path(
    os.getenv("MODELS_OUTPUT_DIR", BASE_DIR / "models_output_json")
).resolve()
STOCKS_DATA_PATH = Path(
    os.getenv("STOCKS_DATA_PATH", BASE_DIR / "stocks_data.csv")
).resolve()

_STOCKS_DATA_CACHE: pd.DataFrame | None = None
_STOCKS_DATA_CACHE_MTIME_NS: int | None = None

# Matches filenames such as:
# portfolio_20260615.json
# model_result_v2_20260615.json
PORTFOLIO_FILE_PATTERN = re.compile(
    r"^.+_(?P<date>\d{8})\.json$",
    re.IGNORECASE,
)


def create_app() -> Flask:
    app = Flask(__name__)

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

    register_routes(app)
    register_error_handlers(app)
    return app


def calculate_model_returns(stock_data: pd.DataFrame, sp500_prices: pd.Series, portfolio_files: list[tuple[datetime, Path]]) -> list[dict]:
    """
    Calculate daily returns for the model portfolio and S&P 500 starting from the first portfolio date.
    """
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
    
    # Initialize cumulative returns
    model_cumulative = 1.0
    sp500_cumulative = 1.0
    
    for i, date in enumerate(available_dates):
        date_str = date.strftime("%Y%m%d")
        
        # Load new portfolio if available for this date or before
        while (portfolio_idx < len(portfolio_files) and 
               portfolio_files[portfolio_idx][0] <= date):
            portfolio_data = read_json_file(portfolio_files[portfolio_idx][1])
            current_portfolio = parse_portfolio_data(portfolio_data)
            portfolio_idx += 1
        
        # Default first-day returns to zero since there is no previous day.
        model_return = 0.0
        sp500_return = 0.0
        
        if i > 0:  # Skip first day since we need previous day for return calculation
            prev_date = available_dates[i-1]
            
            # Calculate model return
            if current_portfolio:
                for stock, weight in current_portfolio.items():
                    if stock in stock_data.columns:
                        try:
                            current_price = stock_data.loc[date, stock]
                            previous_price = stock_data.loc[prev_date, stock]
                            
                            if pd.notna(current_price) and pd.notna(previous_price) and previous_price != 0:
                                stock_return = (current_price - previous_price) / previous_price
                                model_return += weight * stock_return
                        except (KeyError, ValueError):
                            continue
            
            # Calculate S&P 500 return
            try:
                current_sp500 = sp500_prices.loc[date]
                previous_sp500 = sp500_prices.loc[prev_date]
                
                if pd.notna(current_sp500) and pd.notna(previous_sp500) and previous_sp500 != 0:
                    sp500_return = (current_sp500 - previous_sp500) / previous_sp500
            except (KeyError, ValueError):
                sp500_return = 0.0
            
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
        return _STOCKS_DATA_CACHE

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

        portfolios = []
        for portfolio_date, path in files:
            portfolios.append(
                {
                    "portfolio_date": portfolio_date.strftime("%Y-%m-%d"),
                    "filename": path.name,
                    "portfolio": read_json_file(path),
                }
            )

        return jsonify(
            {
                "model_name": model_name,
                "count": len(portfolios),
                "portfolios": portfolios,
            }
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


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def error_response(status_code: int, error: str, message: str):
    return (
        jsonify(
            {
                "error": error,
                "message": message,
                "status": status_code,
            }
        ),
        status_code,
    )


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
