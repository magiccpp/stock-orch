#!/usr/bin/env python
"""Generate the price-only factor models used by stock-orch.

Models
------
factor_momentum_weekly        6-1 month cross-sectional momentum among stocks
                              above their 200-day moving average, inverse-vol
                              weighted, 25 names.
factor_reversal_weekly        5-day cross-sectional reversal among stocks that
                              are still above their 200-day moving average,
                              20 names, equal-risk weighted.
factor_multifactor_weekly     50/50 blend of the two sleeves above.
factor_momentum_long_short_weekly
                              half the momentum book long, half its mirror
                              short: the worst 6-1 month losers among stocks
                              below their 200-day average.
factor_reversal_long_short_weekly
                              half the reversal book long, half its mirror
                              short: the biggest 5-day gainers among stocks
                              below their 200-day average, held short until
                              they recover above it.

All books stay 95% invested whatever the S&P 500 does: an earlier overlay
halved exposure when the index was below its 200-day average, but the index
can whipsaw around that line. Short legs are written as negative weights
with "operation": "short".

Everything is walk-forward: a portfolio file dated D only uses prices up to
D's close, and stock-orch starts applying it on the next trading day.

Usage:
    python generate_factor_models.py --out ../models_output_json
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

TRADING_DAYS = 252
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_prices(csv_path):
    raw = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    raw = raw.drop(columns=[c for c in raw.columns if str(c).startswith("Unnamed")],
                   errors="ignore")
    raw = raw.apply(pd.to_numeric, errors="coerce")
    spx = raw["^GSPC"].copy()
    px = raw.drop(columns=["^GSPC"])
    px = px[[c for c in px.columns if "." not in c]]  # US listings only
    valid = px.notna().rolling(210, min_periods=150).sum()
    px = px.ffill()
    rets = px.pct_change(fill_method=None)
    return px, rets, spx, valid


def rebalance_dates(index, freq, start):
    """Last trading day of each week (or month) since start. The current week
    only counts once it reaches Friday: a provisional mid-week file would be
    replaced by a different one the next day."""
    s = pd.Series(index, index=index)
    if freq == "W":
        g = s.groupby([index.isocalendar().year, index.isocalendar().week]).tail(1)
    else:
        g = s.groupby([index.year, index.month]).tail(1)
    out = [d for d in g.index if d >= pd.Timestamp(start)]
    if freq == "W" and out and out[-1] == index[-1] and index[-1].weekday() < 4:
        out.pop()
    return out


def waterfill(scores, invest, cap):
    s = scores[scores > 0]
    if s.empty or invest <= 0:
        return pd.Series(dtype=float)
    w = s / s.sum() * invest
    for _ in range(20):
        over = w > cap
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        room = ~over
        if not room.any():
            break
        w[room] += excess * s[room] / s[room].sum()
    return w[w > 1e-9]


INVEST = 0.95


def signal_frame(D, px, rets, valid, mom_lb=126, skip=21, vol_lb=63, uptrend=True):
    """Eligible names on D: in an uptrend (above the 200-day SMA) for the long
    books, in a downtrend (below it) for the short books."""
    price = px.loc[D]
    ok = (valid.loc[D] >= 190) & (price > 5)
    sma = px.rolling(200, min_periods=180).mean().loc[D]
    mom = (px.shift(skip) / px.shift(mom_lb) - 1).loc[D]
    vol = rets.rolling(vol_lb, min_periods=40).std().loc[D] * np.sqrt(TRADING_DAYS)
    trend = price > sma if uptrend else price < sma
    df = pd.DataFrame({"price": price, "trend": trend, "mom": mom, "vol": vol})
    return df[ok.fillna(False) & df["trend"].fillna(False)
              & df["mom"].notna() & df["vol"].notna()]


def gen_momentum(px, rets, spx, valid, start, N=25, vol_pen=0.0, cap=0.10, short=False):
    """Long: strongest 6-1 month momentum among uptrending names.
    Short: weakest among downtrending names, as negative weights."""
    out = []
    for D in rebalance_dates(px.index, "W", start):
        df = signal_frame(D, px, rets, valid, uptrend=not short)
        if len(df) < 5:
            out.append((D, {}))
            continue
        z = lambda x: (x - x.mean()) / x.std(ddof=0) if x.std(ddof=0) else x * 0
        score = z(df["mom"]) - vol_pen * z(df["vol"])
        pick = score.sort_values(ascending=short).head(N).index
        w = waterfill(1 / df.loc[pick, "vol"], INVEST, cap)
        out.append((D, (-w if short else w).to_dict()))
    return out


def gen_reversal(px, rets, spx, valid, start, N=20, lookback=5, cap=0.10, short=False):
    """Dip entry + hold-until-trend-break.

    Each week, names that are still eligible (above the 200-day SMA) are
    kept; only vacancies are filled with the biggest 5-day losers. This is
    deliberately sticky: a strict weekly top-N reversal turned over 76% of
    the book per week and returned only +3% over 2025-03..2026-10 after
    costs, so the exit rule -- hold while the uptrend lasts -- is what
    carries the strategy.

    short=True mirrors it: short the biggest 5-day gainers among names below
    their 200-day SMA (a bounce inside a downtrend) and hold the short until
    the name recovers above that average.
    """
    out = []
    prev = []
    for D in rebalance_dates(px.index, "W", start):
        df = signal_frame(D, px, rets, valid, uptrend=not short)
        move = (px / px.shift(lookback) - 1).loc[D]
        df = df.join((move if short else -move).rename("rev"))
        df = df[df["rev"].notna()]
        if len(df) < 5:
            out.append((D, {}))
            continue
        ranked = df.sort_values("rev", ascending=False)
        keep = [t for t in prev if t in ranked.index]
        picks = list(dict.fromkeys(keep + list(ranked.head(N).index)))[:N]
        w = waterfill(pd.Series(1.0, index=picks), INVEST, cap)
        out.append((D, (-w if short else w).to_dict()))
        prev = picks
    return out


def blend(a, b):
    """50/50 mix of two weight histories with the same dates."""
    bmap = dict(b)
    out = []
    for D, wa in a:
        wb = bmap.get(D, {})
        w = {k: 0.5 * wa.get(k, 0.0) + 0.5 * wb.get(k, 0.0) for k in set(wa) | set(wb)}
        out.append((D, {k: v for k, v in w.items() if abs(v) > 1e-9}))
    return out


def write_model(root, name, w_hist, desc):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for old in os.listdir(d):
        os.remove(os.path.join(d, old))
    with open(os.path.join(d, "desc.txt"), "w") as f:
        f.write(desc.strip() + "\n")
    for D, w in w_hist:
        rows = []
        for k, v in sorted(w.items(), key=lambda x: (-abs(x[1]), x[0])):
            if abs(v) <= 1e-9:
                continue
            row = {"id": k, "weight": round(float(v), 6)}
            if v < 0:
                row["operation"] = "short"
            rows.append(row)
        path = os.path.join(d, f"ticket_to_buy_{D.strftime('%Y%m%d')}.json")
        with open(path, "w") as f:
            json.dump(rows, f)


DESCRIPTIONS = {
    "factor_momentum_weekly": (
        "Cross-sectional 6-1 month momentum on US large caps, weekly rebalance. "
        "Requires price above its 200-day moving average; ranks by momentum, "
        "inverse-volatility weighted, 25 names, 10% cap, 95% invested. "
        "Price-only, walk-forward, no look-ahead."
    ),
    "factor_reversal_weekly": (
        "Dip-entry / trend-hold model on US large caps. Each week, buys the "
        "biggest 5-day losers that are still above their 200-day moving "
        "average, then holds each name until it loses that 200-day trend (or "
        "a slot is vacated); 20 names, equal-risk weighted, 10% cap, 95% "
        "invested. Weekly files, but the trend-driven exit keeps turnover low. "
        "Price-only, walk-forward."
    ),
    "factor_multifactor_weekly": (
        "Equal blend of factor_momentum_weekly and factor_reversal_weekly: "
        "50% momentum winners + 50% dip-entry/trend-hold names, both "
        "trend-filtered. Aims to smooth the two sleeves' drawdowns by "
        "combining opposing entry signals. Price-only, walk-forward."
    ),
    "factor_momentum_long_short_weekly": (
        "Long/short 6-1 month momentum on US large caps, weekly rebalance. "
        "Half the book is long the factor_momentum_weekly picks (strongest "
        "momentum above the 200-day average); the other half is short the 25 "
        "weakest-momentum stocks below their 200-day average, inverse-"
        "volatility weighted. Roughly market-neutral. Price-only, walk-forward."
    ),
    "factor_reversal_long_short_weekly": (
        "Long/short dip/bounce model on US large caps. Half the book is long "
        "the factor_reversal_weekly picks (5-day dips in uptrends, held while "
        "the uptrend lasts); the other half is short the biggest 5-day "
        "bounces among stocks below their 200-day average, held until they "
        "recover above it. Roughly market-neutral. Price-only, walk-forward."
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(BASE, "stocks_data.csv"))
    ap.add_argument("--out", default=os.path.join(BASE, "models_output_json"))
    ap.add_argument("--start", default="2025-03-03")
    args = ap.parse_args()

    px, rets, spx, valid = load_prices(args.csv)
    mom = gen_momentum(px, rets, spx, valid, args.start)
    rev = gen_reversal(px, rets, spx, valid, args.start)
    mom_short = gen_momentum(px, rets, spx, valid, args.start, short=True)
    rev_short = gen_reversal(px, rets, spx, valid, args.start, short=True)

    comb = blend(mom, rev)
    mom_ls = blend(mom, mom_short)
    rev_ls = blend(rev, rev_short)

    os.makedirs(args.out, exist_ok=True)
    for name, wh in [("factor_momentum_weekly", mom),
                     ("factor_reversal_weekly", rev),
                     ("factor_multifactor_weekly", comb),
                     ("factor_momentum_long_short_weekly", mom_ls),
                     ("factor_reversal_long_short_weekly", rev_ls)]:
        write_model(args.out, name, wh, DESCRIPTIONS[name])
        print(f"{name}: {len(wh)} files, {wh[0][0].date()}..{wh[-1][0].date()}")


if __name__ == "__main__":
    main()



