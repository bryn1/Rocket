"""Per-indicator signal replay + event-study stats (MC 3874, ARCHITECT-PLAN §3/§4).

Expanding-window replay of every registered indicator (same production path,
bar by bar), edge-triggered transitions, next-OPEN entry, costs per
engine.py's constants. Emits the schema-v1 document (§3). No DB, no network:
frames come from the caller or cache-dir CSVs via the CLI below.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..scoring.rocket_score import INDICATORS
from ..technical.models import Signal

WARMUP_BARS = 60
# Costs are literals copied from rocket/backtest/engine.py:303-305
# (strategy_momentum defaults: commission=0.001, slippage_entry/exit=0.0005).
# engine.py is NOT imported here: importing it opens signals.db paths.
COMMISSION_PCT = 0.1   # % of notional
SLIPPAGE_PCT = 0.05    # % of notional, each side
ROUNDTRIP_PCT = 0.3    # 2 * commission + 2 * slippage
RISK_ONLY_CLASSES = frozenset({"BollingerBands", "ATR", "DonchianChannel"})
ENTRY_RULE = (
    "signal = transition into BUY or SELL (edge-triggered, not per-bar state); "
    "entry at next bar OPEN; exit at close on the first opposite-signal bar at "
    "or after entry, else at horizon end; trades whose exit bar exceeds the "
    "data are dropped and counted in incomplete_dropped; short side symmetric "
    "with sign-flipped returns"
)
_OHLCV_TITLE = {"open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume"}


def to_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Rename lowercase OHLCV columns to the Title-case shape all 34 indicators
    accept (ARCHITECT-PLAN V4: 6 pattern indicators KeyError on lowercase).
    Idempotent; non-OHLCV columns (e.g. ``date``) pass through.
    """
    return df.rename(columns={
        c: _OHLCV_TITLE[str(c).lower()]
        for c in df.columns if str(c).lower() in _OHLCV_TITLE
    })


def _last_bar(prepared: dict) -> str:
    """Max last-bar date across frames ('' when no date column/index)."""
    dates = []
    for frame, _, _ in prepared.values():
        if "date" in frame.columns:
            dates.append(pd.to_datetime(frame["date"]).iloc[-1])
        elif isinstance(frame.index, pd.DatetimeIndex):
            dates.append(frame.index[-1])
    return max(dates).strftime("%Y-%m-%d") if dates else ""


def _replay(ind, df: pd.DataFrame):
    """Expanding-window signal series per §4: calculate(df.iloc[:t+1]) per bar
    from WARMUP_BARS on. A raising calculate() counts as one calc_error and
    that bar's signal is HOLD. Returns ({bar: Signal}, errors, meta).
    """
    sig, errors, meta = {}, 0, None
    for t in range(WARMUP_BARS, len(df)):
        try:
            res = copy.deepcopy(ind).calculate(df.iloc[:t + 1])
            sig[t] = res.signal
            if meta is None:
                meta = (res.name, res.category.value)
        except Exception:
            errors += 1
            sig[t] = Signal.HOLD
    return sig, errors, meta


def _block(gross: list[float]) -> dict:
    """Event-study block (§3): null for every stat but n when n == 0."""
    if not gross:
        return {"n": 0, "hit_rate": None, "avg_gross_pct": None, "avg_net_pct": None,
                "median_net_pct": None, "best_pct": None, "worst_pct": None}
    net = [g - ROUNDTRIP_PCT for g in gross]
    return {
        "n": len(net),
        "hit_rate": round(sum(1 for v in net if v > 0) / len(net), 4),
        "avg_gross_pct": round(statistics.fmean(gross), 4),
        "avg_net_pct": round(statistics.fmean(net), 4),
        "median_net_pct": round(statistics.median(net), 4),
        "best_pct": round(max(net), 4), "worst_pct": round(min(net), 4),
    }


def _evidence(primary_nets: list[float], calc_errors: int,
              bars_evaluated: int, tickers: int):
    """evidence_weight / evidence_t per §3: error check first, then n, then
    a one-sided t on the PRIMARY-horizon long side (t null when n < 2).
    """
    n = len(primary_nets)
    t = None
    if n >= 2:
        sd = statistics.stdev(primary_nets)
        t = 0.0 if sd == 0 else statistics.fmean(primary_nets) / (sd / n ** 0.5)
    if calc_errors > 0.5 * bars_evaluated * tickers:
        weight = "error"
    elif n < 20:
        weight = "insufficient"
    else:
        weight = ("strong" if t >= 2.0 else "moderate" if t >= 1.0
                  else "weak" if t > 0 else "none")
    return weight, (None if t is None else round(t, 2))


def _eval_indicator(ind, prepared: dict, horizons: tuple[int, ...],
                    primary_horizon: int, bars_evaluated: int):
    """Replay one indicator over all tickers; returns its §3 block plus the
    per-side count of trades dropped because their exit bar exceeds the data.
    """
    name = category = None
    calc_errors = n_buy = n_sell = 0
    incomplete = {"long": 0, "short": 0}
    gross = {(h, side): [] for h in horizons for side in ("long", "short")}
    for frame, opens, closes in prepared.values():
        sig, errors, meta = _replay(ind, frame)
        calc_errors += errors
        if meta is not None and name is None:
            name, category = meta
        prev = Signal.HOLD
        for t in range(WARMUP_BARS, len(frame)):
            cur = sig[t]
            if cur != Signal.HOLD and cur != prev:  # edge trigger (§4)
                side = "long" if cur == Signal.BUY else "short"
                if side == "long":
                    n_buy += 1
                else:
                    n_sell += 1
                opposite = Signal.SELL if side == "long" else Signal.BUY
                entry = t + 1  # next bar's OPEN is the first tradeable print
                opp_bar = next((j for j in range(entry, len(frame))
                                if sig.get(j) == opposite), None)
                for h in horizons:
                    x = opp_bar if opp_bar is not None and opp_bar <= t + h else t + h
                    if x >= len(frame):
                        incomplete[side] += 1
                        continue
                    entry_px, exit_px = opens[entry], closes[x]
                    g = ((exit_px - entry_px) if side == "long"
                         else (entry_px - exit_px)) / entry_px * 100
                    gross[(h, side)].append(g)
            prev = cur
    hor_block = {str(h): {s: _block(gross[(h, s)]) for s in ("long", "short")}
                 for h in horizons}
    weight, t_stat = _evidence(gross[(primary_horizon, "long")], calc_errors,
                               bars_evaluated, len(prepared))
    block = {
        "name": name or type(ind).__name__,
        "category": category or getattr(ind, "category_name", "") or "",
        "risk_only": type(ind).__name__ in RISK_ONLY_CLASSES,
        "bars_evaluated": bars_evaluated, "calc_errors": calc_errors,
        "n_signals": n_buy + n_sell, "n_buy": n_buy, "n_sell": n_sell,
        "horizons": hor_block, "evidence_weight": weight, "evidence_t": t_stat,
    }
    return block, incomplete


def run_indicator_eval(frames: dict[str, pd.DataFrame],
                       universe: dict[str, list[str]],
                       horizons: tuple[int, ...] = (5, 10, 20),
                       primary_horizon: int = 10,
                       indicators: list | None = None) -> dict:
    """Build the schema-v1 document (§3) by replaying every indicator over
    every frame. Raises only on total failure (no frames/indicators, or all
    indicators erroring); partial failure is reported inside the document.
    Deterministic: same frames => identical output except ``generated_at``.
    """
    if not frames:
        raise ValueError("run_indicator_eval: no frames supplied")
    inds = list(INDICATORS if indicators is None else indicators)  # all 34 by default
    if not inds:
        raise ValueError("run_indicator_eval: no indicators supplied")
    prepared = {}
    for label, df in frames.items():
        frame = to_indicator_frame(df)
        prepared[label] = (frame, frame["Open"].to_numpy(float), frame["Close"].to_numpy(float))
    bars_evaluated = int(sum(max(0, len(f) - WARMUP_BARS) for f in prepared.values())
                         / len(prepared) + 0.5)
    doc_indicators: dict[str, dict] = {}
    total_incomplete = {"long": 0, "short": 0}
    for ind in sorted(inds, key=lambda i: type(i).__name__):
        block, incomplete = _eval_indicator(ind, prepared, horizons,
                                            primary_horizon, bars_evaluated)
        doc_indicators[type(ind).__name__] = block
        for side in total_incomplete:
            total_incomplete[side] += incomplete[side]
    if all(b["calc_errors"] > 0 for b in doc_indicators.values()):
        raise RuntimeError("run_indicator_eval: every indicator errored")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_last_bar": _last_bar(prepared),
        "engine": {"indicators_registered": len(inds), "tickers": len(frames),
                   "warmup_bars": WARMUP_BARS},
        "universe": {k: list(v) for k, v in universe.items()},
        "costs_pct": {"commission": COMMISSION_PCT, "slippage": SLIPPAGE_PCT,
                      "roundtrip": ROUNDTRIP_PCT},
        "horizons_days": list(horizons), "primary_horizon_days": primary_horizon,
        "entry_rule": ENTRY_RULE, "incomplete_dropped": total_incomplete,
        "indicators": doc_indicators,
    }


def _read_cache(path: Path) -> pd.DataFrame:
    """Read a cache CSV (V6: old caches may lack a date column)."""
    df = pd.read_csv(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def main(argv: list[str] | None = None) -> int:
    """CLI per §4: replay cached CSVs (labels = stems), write the JSON artifact; no network, no DB."""
    ap = argparse.ArgumentParser(
        prog="python3 -m rocket.backtest.indicator_eval",
        description="Per-indicator replay + evidence stats (schema v1).")
    ap.add_argument("--cache-dir", default="source/data/raw")
    ap.add_argument("--tickers", default="",
                    help="comma-separated CSV stems (default: all CSVs)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    cache = Path(args.cache_dir)
    stems = sorted([s.strip() for s in args.tickers.split(",") if s.strip()]
                   if args.tickers else (p.stem for p in cache.glob("*.csv")))
    if not stems:
        raise SystemExit(f"no CSVs found under {cache}")
    frames = {s: _read_cache(cache / f"{s}.csv") for s in stems}
    doc = run_indicator_eval(frames, {"cli": stems})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(f"wrote {out} ({len(doc['indicators'])} indicators, "
          f"{len(stems)} tickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
