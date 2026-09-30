#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DI3 runner — scoring + top-25 export over the DI2 store.

Reads the OHLCV historical parquet the DI2 fetch wrote into the data-collect
store, computes a composite 0-100 score per ticker from its price series
(momentum / trend / stability / liquidity), ranks all scored tickers, and
exports the TOP-K (default 25) as a dated CSV + JSON deliverable plus an
enriched scores parquet.

Contract: architecture record 627.9 cycle-3 (M3 -> M4 -> M5/M6 scoring). The
store boundary is /srv/workspace/svarkor-rocket-25k-data-collect/store/ from
DI2 (685.2). This stage (DI3 / 685.3) consumes that store — it does NOT fetch.

Append-only: the scores parquet is written to a NEW dated _di3 filename and
never overwrites the DI2 placeholder scores file.

Modes
-----
  (no flag)  real run against the real data-collect store
  --dry-run  copy the store to a scratch dir first and run there (bounded test)

Robust to the ragged store a real fetch leaves behind: a ticker with <2
observations is scored 0.0 and flagged insufficient_data, never a crash.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import shutil
import sys
from dataclasses import dataclass
from datetime import date, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - venv guaranteed at runtime
    pq = None

# ---- fixed paths ------------------------------------------------------------
PN = Path("/srv/workspace/svarkor-rocket-25k-data-collect")
REAL_STORE = PN / "store"
TEDDY_DIR = PN / "teddy"
SCRATCH_ROOT = Path("/home/teddy/di3-scratch")

TOP_K_DEFAULT = 25

# ---- composition weights (must sum to 100) ----------------------------------
W_MOMENTUM = 40
W_TREND = 30
W_STABILITY = 20
W_LIQUIDITY = 10


# ---------------------------------------------------------------------------
# Pure scoring core (identical to the TDD-verified logic; see tests in scratch)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoreResult:
    ticker: str
    region: str
    source_symbol: Optional[str]
    score: Optional[float]
    price: Optional[float]
    last_date: Optional[str]
    obs: int
    sufficient_data: bool
    reason: str


def _bounded(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_div(num: float, den: float) -> Optional[float]:
    if den is None or not math.isfinite(float(den)) or float(den) == 0.0:
        return None
    return float(num) / float(den)


def _momentum_subscore(ret: Optional[float]) -> float:
    if ret is None or not math.isfinite(float(ret)):
        return 0.0
    r = _bounded(float(ret), -2.0, 2.0)
    return _bounded(0.5 + r / 4.0, 0.0, 1.0)


def _trend_subscore(series: pd.Series) -> float:
    closes = series.dropna().astype(float)
    n = len(closes)
    if n < 20:
        return 0.5
    recent = closes.tail(20).mean()
    base = closes.iloc[: n - 20].mean()
    if base is None or not math.isfinite(float(base)) or float(base) == 0.0:
        return 0.5
    diff = (float(recent) - float(base)) / float(base)
    return _bounded(0.5 + diff, 0.0, 1.0)


def _stability_subscore(series: pd.Series, rets_std: Optional[float]) -> float:
    if rets_std is None or not math.isfinite(float(rets_std)):
        return _stability_from_returns(series)
    annualized = float(rets_std) * math.sqrt(252)
    return _bounded(1.0 - annualized, 0.0, 1.0)


def _stability_from_returns(series: pd.Series) -> float:
    closes = series.dropna().astype(float)
    if len(closes) < 2:
        return 0.0
    pct = closes.pct_change().dropna()
    if pct.empty:
        return 0.0
    std = float(pct.std())
    if not math.isfinite(std):
        return 0.0
    return _bounded(1.0 - std * math.sqrt(252), 0.0, 1.0)


def _liquidity_subscore(mean_volume: Optional[float]) -> float:
    if mean_volume is None or not math.isfinite(float(mean_volume)):
        return 0.0
    v = max(float(mean_volume), 0.0)
    if v <= 0.0:
        return 0.0
    return _bounded((math.log10(v) - 2.0) / 6.0, 0.0, 1.0)


def compute_ticker_score(ticker: str, region: str, source_symbol, df: pd.DataFrame) -> ScoreResult:
    if df is None or len(df) == 0:
        return ScoreResult(ticker, region, source_symbol, None, None, None, 0, False, "no_data")
    if "date" in df.columns:
        df = df.sort_values("date")
    closes = pd.to_numeric(df["close"], errors="coerce") if "close" in df.columns else pd.Series(dtype=float)
    n_obs = int(closes.notna().sum())
    if n_obs < 2:
        price = float(closes.dropna().iloc[0]) if n_obs == 1 else None
        return ScoreResult(
            ticker, region, source_symbol, 0.0, price,
            str(df["date"].iloc[-1]) if "date" in df.columns else None,
            n_obs, False, "insufficient_data",
        )
    first_close = float(closes.dropna().iloc[0])
    last_close = float(closes.dropna().iloc[-1])
    period_return = _safe_div(last_close - first_close, first_close)
    pct = closes.dropna().pct_change().dropna()
    rets_std = float(pct.std()) if len(pct) > 0 and math.isfinite(float(pct.std())) else None
    vol_col = df["volume"] if "volume" in df.columns else pd.Series(dtype=float)
    vols = pd.to_numeric(vol_col, errors="coerce").replace(0, np.nan).dropna()
    mean_volume = float(vols.mean()) if len(vols) > 0 else None
    m = _momentum_subscore(period_return)
    t = _trend_subscore(closes)
    st = _stability_subscore(closes, rets_std)
    liq = _liquidity_subscore(mean_volume)
    score = round(W_MOMENTUM * m + W_TREND * t + W_STABILITY * st + W_LIQUIDITY * liq, 4)
    return ScoreResult(
        ticker, region, source_symbol, score, last_close,
        str(df["date"].iloc[-1]), n_obs, True, "scored",
    )


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------
def load_all_historical(store_dir: Path) -> pd.DataFrame:
    """Concat every historical_*.parquet under store_dir/historical."""
    files = sorted(glob.glob(str(store_dir / "historical" / "historical_*.parquet")))
    if not files:
        raise SystemExit(f"no historical parquet under {store_dir / 'historical'}")
    frames = []
    for f in files:
        t = pq.read_table(f)
        frames.append(t.to_pandas())
    return pd.concat(frames, ignore_index=True)


def build_scores_df(hist: pd.DataFrame) -> pd.DataFrame:
    """Compute a ScoreResult per ticker and flatten to a tidy frame."""
    rows = []
    for (ticker, region), grp in hist.groupby(["ticker", "region"]):
        # source_symbol may differ per row; take the first non-null within the group
        if "source_symbol" in grp.columns:
            src_vals = grp["source_symbol"].dropna()
            src_val = str(src_vals.iloc[0]) if len(src_vals) > 0 else None
        else:
            src_val = None
        r = compute_ticker_score(ticker, region, src_val, grp)
        rows.append({
            "ticker": r.ticker,
            "region": r.region,
            "score": r.score,
            "price": r.price,
            "obs": r.obs,
            "sufficient_data": r.sufficient_data,
            "reason": r.reason,
            "source_symbol": r.source_symbol,
            "last_date": r.last_date,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_topk(scores: pd.DataFrame, top_k: int, out_dir: Path, tag: str) -> dict:
    """Write top-K (CSV+JSON) + full scores parquet (append-only _di3)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    top = scores.head(top_k)

    csv_path = out_dir / f"685.3-top{top_k}-{tag}.csv"
    json_path = out_dir / f"685.3-top{top_k}-{tag}.json"

    cols = ["ticker", "region", "rank", "score", "price", "obs", "sufficient_data", "reason", "source_symbol", "last_date"]
    top[cols].to_csv(csv_path, index=False)
    payload = top[cols].to_dict(orient="records")
    for rec in payload:
        rec["score"] = None if rec["score"] is None else float(rec["score"])
        rec["price"] = None if rec["price"] is None else float(rec["price"])
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Append-only scores parquet: never clobber the DI2 placeholder.
    scores_out = out_dir / f"scores_{tag}_di3.parquet"
    scores_out.write_bytes(_parquet_bytes(scores))
    return {
        "csv": str(csv_path),
        "json": str(json_path),
        "scores_parquet": str(scores_out),
        "n_scored": int(scores["reason"].eq("scored").sum()),
        "n_insufficient": int(scores["reason"].eq("insufficient_data").sum()),
        "n_rows_exported": int(len(top)),
    }


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    import io
    buf = io.BytesIO()
    from pyarrow import Table
    t = Table.from_pandas(df, preserve_index=False)
    import pyarrow.parquet as pqq
    pqq.write_table(t, buf, compression="snappy")
    return buf.getvalue()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="DI3 scoring + top-K export over the DI2 store")
    ap.add_argument("--store", type=str, default=str(REAL_STORE))
    ap.add_argument("--top-k", type=int, default=TOP_K_DEFAULT)
    ap.add_argument("--out-dir", type=str, default=str(TEDDY_DIR))
    ap.add_argument("--dry-run", action="store_true", help="stage a store copy in scratch and run there")
    ap.add_argument("--tag", type=str, default=None, help="date tag (default = UTC today)")
    args = ap.parse_args(argv)

    tag = args.tag or date.today().isoformat()

    if args.dry_run:
        scratch_store = SCRATCH_ROOT / "store"
        if scratch_store.exists():
            shutil.rmtree(scratch_store)
        shutil.copytree(args.store, scratch_store)
        store_dir = scratch_store
        print(f"DRY-RUN: staged store copy at {scratch_store}")
    else:
        store_dir = Path(args.store)

    hist = load_all_historical(store_dir)
    print(f"store: {store_dir} | historical files: {len(glob.glob(str(store_dir / 'historical' / 'historical_*.parquet')))}")
    print(f"historical rows loaded: {len(hist)} | unique tickers: {hist['ticker'].nunique()}")

    scores = build_scores_df(hist)
    print(f"scores computed: {len(scores)} tickers | scored: {scores['reason'].eq('scored').sum()} | "
          f"insufficient_data: {scores['reason'].eq('insufficient_data').sum()}")

    out = export_topk(scores, args.top_k, Path(args.out_dir), tag)
    print("export:")

    for k, v in out.items():
        print(f"  {k}: {v}")

    top = scores.head(args.top_k)
    print("\nTOP RANKING:")
    for _, r in top.iterrows():
        sc = "n/a" if r["score"] is None else f"{r['score']:.2f}"
        print(f"  #{int(r['rank']):>2} {r['ticker']:<12} region={r['region']:<10} score={sc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
