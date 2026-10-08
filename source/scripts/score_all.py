"""Parallel nightly scoring over the CSV store — Pool(8), zero accumulation.

Worker contract (DESIGN §6, the 685.3 memory lesson): one call reads ONE CSV
via store_io, scores it through the app seam (``to_indicator_frame`` +
``app._compute_all_indicators`` + ``app._score_from_summary`` — the same path
the 35-ticker generator used, seam C1), releases the frame and returns a
today-shape row dict + the last-bar date. Workers never hold frames between
calls; nothing is ever concatenated. The heavy ``app`` (Dash) import happens
in the POOL INITIALIZER, once per worker, never per task.

workers=0 runs inline (single process — test seam and degraded-host fallback;
identical code path through _score_one). Journal output is capped per F9:
counts + first 10 error strings, never the full list (~100 KB lines on a
90 %-live day).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io  # noqa: E402
from full_universe import CACHE_DIR, UniversePlan  # noqa: E402

POOL_WORKERS = 8      # 14 cores, ~0.3 GB/worker incl. app/dash (DESIGN §6)
ERROR_LOG_CAP = 10    # F9 journal cap


def _init_worker() -> None:
    """Pool initializer: the heavy app import resolves once per worker."""
    import app  # noqa: F401 — Dash import; bound into this worker's process


def _score_one(task: tuple[str, str, str]) -> dict:
    """Picklable module-level worker: (ticker, region, cache_dir_str) -> meta.

    meta = {ticker, region, row|None, last_bar, has_usable_store, error}.
    Reads one CSV, scores, releases. Never raises across the pool boundary.
    """
    ticker, region, cache_dir_str = task
    meta: dict = {"ticker": ticker, "region": region, "row": None,
                  "last_bar": "", "has_usable_store": False, "error": ""}
    try:
        df = store_io.read_store(ticker, region,
                                 cache_dir=Path(cache_dir_str))
        if df is None or df.empty:
            return meta
        meta["has_usable_store"] = True
        meta["last_bar"] = store_io.last_bar_date(df)
        if store_io.usable_bars(df) < store_io.MIN_SCORE_ROWS:
            return meta
        from rocket.backtest.indicator_eval import to_indicator_frame
        from app import _compute_all_indicators, _score_from_summary
        idf = to_indicator_frame(df)
        summary, _ = _compute_all_indicators(idf)
        rs = _score_from_summary(summary, ticker=ticker,
                                 region=region)["rocket_score"]
        signal = getattr(summary, "signal", None) or (
            "BUY" if summary.buy_count > summary.sell_count else
            "SELL" if summary.sell_count > summary.buy_count else "HOLD")
        meta["row"] = {
            "ticker": ticker, "signal": signal, "overall": rs.overall_score,
            "momentum": rs.momentum_score, "trend": rs.trend_score,
            "volatility": rs.volatility_score, "volume": rs.volume_score,
            "close": round(float(idf["Close"].iloc[-1]), 2),
        }
    except Exception as exc:  # noqa: BLE001 — one ticker must not kill the run
        meta["error"] = f"{ticker}: {exc}"
    return meta


def score_all(plan: UniversePlan, *, cache_dir: Path | None = None,
              workers: int = POOL_WORKERS) -> dict:
    """Score every planned ticker from the store; returns::

        {"results": {region: [row dicts]},        # today's demo_render shape
         "meta":    {ticker: meta dict},          # manifest + §4 partition
         "last_bars": {region: [ISO date]},        # per-region, §4 manifest
         "scalars": {...}}

    No frames ever leave a worker.
    """
    cdir = str(cache_dir or CACHE_DIR)
    tasks = [(t, region, cdir) for region in plan.order
             for t in plan.regions[region]]
    if workers and workers > 0:
        with Pool(workers, initializer=_init_worker) as pool:
            metas = pool.map(_score_one, tasks, chunksize=20)
    else:
        _init_worker()
        metas = [_score_one(t) for t in tasks]

    results: dict[str, list] = {region: [] for region in plan.order}
    meta_by_ticker: dict[str, dict] = {}
    for m in metas:
        meta_by_ticker[m["ticker"]] = m
        if m["row"] is not None:
            results.setdefault(m["region"], []).append(m["row"])
    errors = [m["error"] for m in metas if m["error"]]
    scored_regions = {r: len(v) for r, v in results.items()}
    return {
        "results": results,
        "meta": meta_by_ticker,
        "last_bars": {r: [m["last_bar"] for m in metas
                          if m["region"] == r and m["last_bar"]]
                      for r in plan.order},
        "scalars": {
            "attempted": len(tasks),
            "scored_regions": scored_regions,
            "scored_total": sum(scored_regions.values()),
            "error_count": len(errors),
            "errors_shown": errors[:ERROR_LOG_CAP],  # F9 cap
            "errors_truncated": max(0, len(errors) - ERROR_LOG_CAP),
        },
    }


def summarize(scalars: dict) -> str:
    """Capped journal line (F9): counts + first ERROR_LOG_CAP error strings."""
    tail = (f" (+{scalars['errors_truncated']} more)"
            if scalars.get("errors_truncated") else "")
    return (f"scored {scalars['scored_total']}/{scalars['attempted']}; "
            f"errors={scalars['error_count']}{tail}: "
            f"{'; '.join(scalars['errors_shown'])}")


def main() -> None:
    """CLI for manual runs/measurements (nightly path calls score_all())."""
    from full_universe import load_plan
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=POOL_WORKERS)
    ap.add_argument("--json-out", type=Path, default=None,
                    help="write scalars+last_bars JSON (scratch path only)")
    args = ap.parse_args()
    plan = load_plan()
    t0 = time.time()
    out = score_all(plan, workers=args.workers)
    print(summarize(out["scalars"]))
    print(f"wall {time.time() - t0:.1f}s on {args.workers or 'inline'} workers")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(
            {"scalars": out["scalars"], "last_bars": out["last_bars"],
             "generated": str(date.today())}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
