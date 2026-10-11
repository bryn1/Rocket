"""Per-trial grid metrics: panel daily series + the §4 metric set (T-BT4).

Pure functions (no paths, no clock): the grid (bt_combo) hands these its
per-ticker round-trip lists and opened-bar counts.

  * panel_daily_series — §4 series definition: a trade's WHOLE net % return
    is attributed to its EXIT bar's date; per stored trading day r_d =
    equal-weight mean over the tickers that HAVE a bar that day (0 where
    flat; a ticker not trading that day is not "flat", it is absent).
  * metrics_block — n trades, hit-rate, mean/median net %, seeded stationary
    bootstrap 95 % CI on the per-trade mean (random.Random(BOOTSTRAP_SEED),
    stdlib — DESCRIPTIVE; the gate is DSR on the daily series, §4), and
    turnover (fraction of bars in-position, trades/ticker/year at 252 bars).

Primary endpoint throughout §4: LONG side, H = 10 (primary horizon).
"""
from __future__ import annotations

import random
import statistics

BOOTSTRAP_SEED = 20261009        # §4 pinned seed
BOOTSTRAP_REPS = 999             # deterministic, stdlib only
BLOCK_AVG_LEN = 10.0             # stationary-bootstrap mean block length
BARS_PER_YEAR = 252              # daily bars (§2: interval=1d)


def panel_daily_series(days: list[str], ticker_bars: dict[str, int],
                       trades: list[dict], side: str = "long",
                       horizon: int | None = None) -> list[float]:
    """Exit-attributed panel-mean daily series over the fold's `days`.
    trades: dicts with exit_date, side, horizon and net_pct (net of
    ROUNDTRIP_PCT). horizon=10 IS THE §4 GATE SERIES (primary endpoint
    pinned; h=5/20 are secondary slices and must never enter it — their
    trades share entries and overlap, the exact condition §4 excludes);
    horizon=None keeps the generic all-horizon series for DESCRIPTIVE use."""
    by_exit: dict[str, float] = {}
    for tr in trades:
        if tr["side"] != side:
            continue
        if horizon is not None and tr.get("horizon") != horizon:
            continue
        by_exit[tr["exit_date"]] = by_exit.get(tr["exit_date"], 0.0) + tr["net_pct"]
    return [by_exit.get(d, 0.0) / ticker_bars[d] if ticker_bars.get(d) else 0.0
            for d in days]


def _percentile(sorted_vals: list[float], q: float) -> float:
    idx = min(int(q * (len(sorted_vals) - 1) + 0.5), len(sorted_vals) - 1)
    return sorted_vals[idx]


def bootstrap_ci95(values: list[float]) -> tuple[float, float] | None:
    """Stationary (geometric-block) bootstrap CI on the mean, seeded fixed —
    byte-reproducible run to run, no wall clock."""
    if len(values) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n, means = len(values), []
    for _ in range(BOOTSTRAP_REPS):
        total, i = 0.0, rng.randrange(n)
        for _ in range(n):
            total += values[i]
            if rng.random() < 1.0 / BLOCK_AVG_LEN or i + 1 >= n:
                i = rng.randrange(n)          # new block: fresh start index
            else:
                i += 1                        # continue the current block
        means.append(total / n)
    means.sort()
    return round(_percentile(means, 0.025), 4), round(_percentile(means, 0.975), 4)


def metrics_block(trades: list[dict], side: str, horizon: int,
                  bars_in_position: int, bars_total: int,
                  tickers: int) -> dict:
    """The §4 descriptive metric set for one (combo, side, horizon) block."""
    nets = [t["net_pct"] for t in trades
            if t["side"] == side and t["horizon"] == horizon]
    if not nets:
        return {"n": 0, "hit_rate": None, "mean_net_pct": None,
                "median_net_pct": None, "bootstrap_ci95": None,
                "in_position_fraction": round(
                    bars_in_position / bars_total, 4) if bars_total else None,
                "trades_per_ticker_year": None}
    return {
        "n": len(nets),
        "hit_rate": round(sum(1 for v in nets if v > 0) / len(nets), 4),
        "mean_net_pct": round(statistics.fmean(nets), 4),
        "median_net_pct": round(statistics.median(nets), 4),
        "bootstrap_ci95": bootstrap_ci95(nets),
        "in_position_fraction": round(bars_in_position / bars_total, 4)
        if bars_total else None,
        "trades_per_ticker_year": round(
            len(nets) / tickers * BARS_PER_YEAR / bars_total, 4)
        if tickers and bars_total else None,
    }
