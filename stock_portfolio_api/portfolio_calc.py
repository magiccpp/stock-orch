from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


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


def build_portfolio_return_windows(
    stock_data: pd.DataFrame | None,
    portfolios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Given a model's portfolios sorted ascending by portfolio_date -- each a
    dict with at least "filename", "portfolio_date" (datetime-like) and
    "holdings" (dict[str, float]) -- compute the return-window fields the
    API serves for each one: returns_until_next_change (holdings' returns
    from this portfolio's date up to the next portfolio's date, or the
    latest available trading date for the current portfolio),
    return_until_date, and is_current_portfolio.
    """
    last_available_date = (
        stock_data.index.max()
        if stock_data is not None and not stock_data.empty
        else None
    )

    windows = []
    n = len(portfolios)
    for i, entry in enumerate(portfolios):
        is_current = i + 1 >= n
        end_date = last_available_date if is_current else portfolios[i + 1]["portfolio_date"]

        returns_until_next_change: dict[str, float] = {}
        if stock_data is not None and end_date is not None:
            returns_until_next_change = calculate_returns_until_next_change(
                stock_data, entry["holdings"], entry["portfolio_date"], end_date
            )

        windows.append(
            {
                "filename": entry["filename"],
                "returns_until_next_change": returns_until_next_change,
                "return_until_date": (
                    end_date.date() if hasattr(end_date, "date") else end_date
                ),
                "is_current_portfolio": is_current,
            }
        )

    return windows


def calculate_model_returns(
    stock_data: pd.DataFrame,
    sp500_prices: pd.Series,
    portfolios: list[dict[str, Any]],
) -> list[dict]:
    """
    Calculate daily returns for the model portfolio and S&P 500 starting
    from the first portfolio date. portfolios must be sorted ascending by
    portfolio_date, each a dict with "portfolio_date" (datetime-like) and
    "holdings" (dict[str, float]).
    """
    if not portfolios:
        return []

    returns_data = []
    current_portfolio: dict[str, float] = {}
    portfolio_idx = 0

    first_portfolio_date = portfolios[0]["portfolio_date"]
    available_dates = stock_data.index[stock_data.index >= first_portfolio_date]

    if len(available_dates) == 0:
        return []

    # Precompute per-stock returns using each stock's own consecutive valid trading dates.
    # dropna() ensures pct_change() steps over NaN rows (e.g. different market holidays).
    stock_returns_map: dict[str, pd.Series] = {
        col: stock_data[col].dropna().pct_change()
        for col in stock_data.columns
    }
    sp500_pct = sp500_prices.dropna().pct_change()

    model_cumulative = 1.0
    sp500_cumulative = 1.0

    for i, date in enumerate(available_dates):
        while (
            portfolio_idx < len(portfolios)
            and portfolios[portfolio_idx]["portfolio_date"] < date
        ):
            current_portfolio = portfolios[portfolio_idx]["holdings"]
            portfolio_idx += 1

        model_return = 0.0
        sp500_return = 0.0

        if i > 0:  # Skip first day since we need previous day for return calculation
            if current_portfolio:
                stocks = [
                    s for s in current_portfolio
                    if s in stock_returns_map and date in stock_returns_map[s].index
                ]
                if stocks:
                    weights = pd.Series({s: current_portfolio[s] for s in stocks})
                    returns = pd.Series({s: stock_returns_map[s].loc[date] for s in stocks})
                    valid = returns.notna()
                    if valid.any():
                        model_return = float(weights[valid].dot(returns[valid]))
            if date in sp500_pct.index and pd.notna(sp500_pct.loc[date]):
                sp500_return = float(sp500_pct.loc[date])

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
