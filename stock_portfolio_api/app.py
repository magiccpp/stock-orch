from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request
from flask_compress import Compress
from flasgger import Swagger

import db
from auth import register_auth_routes
from errors import ApiError, error_response
from portfolio_calc import calculate_range_return
from utils import client_ip


VISITS_DATA_PATH = Path(
    os.getenv("VISITS_DATA_PATH", "/app/data/visits.json")
).resolve()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable must be set")

_visits_lock = threading.Lock()


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
                    "Read stock-AI model portfolios and returns from Postgres "
                    "(populated by the daily import_data.py job)."
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


def validate_model_name(model_name: str) -> None:
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


def require_model(model_name: str) -> None:
    validate_model_name(model_name)
    if not db.model_exists(model_name):
        raise ApiError(
            404,
            "Model not found",
            f"Model '{model_name}' does not exist.",
        )


def _date_str(value) -> str | None:
    return value.strftime("%Y-%m-%d") if value is not None else None


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
          Returns the names of all models imported into the database.
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
        """
        models = db.get_all_model_names()
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
            description: Exact model name
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
            description: Model or matching portfolio not found
        """
        require_model(model_name)
        latest = db.get_latest_portfolio(model_name)

        if latest is None:
            return error_response(
                404,
                "No portfolio files found",
                f"No portfolios have been imported yet for model '{model_name}'.",
            )

        return jsonify(
            {
                "model_name": model_name,
                "portfolio_date": _date_str(latest["portfolio_date"]),
                "filename": latest["filename"],
                "portfolio": latest["portfolio_data"],
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
            description: Exact model name
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
            description: Model not found
        """
        require_model(model_name)
        rows = db.get_model_returns(model_name)

        returns_data = [
            {
                "date": _date_str(row["return_date"]),
                "model_return": row["model_return"],
                "sp500_return": row["sp500_return"],
                "model_cumulative_return": row["model_cumulative_return"],
                "sp500_cumulative_return": row["sp500_cumulative_return"],
            }
            for row in rows
        ]

        return jsonify({"model_name": model_name, "returns": returns_data})

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
            description: Exact model name
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
            description: Model not found
        """
        require_model(model_name)
        rows = db.get_portfolios_for_model(model_name)

        portfolios = [
            {
                "portfolio_date": _date_str(row["portfolio_date"]),
                "filename": row["filename"],
                "portfolio": row["portfolio_data"],
                "returns_until_next_change": row["returns_until_next_change"],
                "return_until_date": _date_str(row["return_until_date"]),
                "is_current_portfolio": row["is_current_portfolio"],
            }
            for row in rows
        ]

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
            description: Comma-separated stock symbols
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

        returns = {}
        for symbol in symbols:
            price_series = db.get_stock_price_series(symbol)
            if price_series.empty:
                continue
            stock_return = calculate_range_return(price_series, start_date, end_date)
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
            description: Exact model name
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
        require_model(model_name)
        return jsonify(
            {
                "model_name": model_name,
                "description": db.get_model_description(model_name),
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
        summary: Check whether the API process is running and can reach the database
        responses:
          200:
            description: Service is running
        """
        try:
            model_count = len(db.get_all_model_names())
            db_ok = True
        except Exception:
            model_count = 0
            db_ok = False

        return jsonify(
            {
                "status": "ok",
                "database_reachable": db_ok,
                "model_count": model_count,
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
