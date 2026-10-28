"""Parallel nightly scoring over the CSV store — TWO-PASS, Pool(8), zero accumulation.

Wire law (MC 11474 B2, PLAN-c3 §1/§3, owner ruling B): every bar scores through
the 34-indicator island via ``nightly_score.score_row`` — the old app seam
(``_compute_all_indicators`` + ``_score_from_summary``) is retired from this
driver. The two-pass loop lives HERE (not beside it in generate_demo_page.py:
no second scoring driver).

Two-pass law (D2, ratification DECISIONS.md): pass 1 scores every ticker
``social=False``; the preliminary top ``ALLA_CAP`` (500, full_universe.py:60 —
the page's visible universe) by preliminary ``overall`` are re-scored in pass
2 ``social=True`` through the adapter (per-call timeout N=2.0 s rides the
adapter, never a hidden default). C3 merge, by ticker, BEFORE results/
partition: a pass-2 meta replaces the pass-1 meta ONLY when it carries a row;
on a pass-2 error the pass-1 row is KEPT and the error rides ``meta['error']``
— no ticker appears twice in ``results``, none loses a scored pass-1 row.

C1 index feed: ``score_all()`` reads the ≤4 ``REGION_INDEX`` index CSVs ONCE
per run via ``store_io`` (missing file = None = the tested CHOP×0.5 fallback)
and EVERY ``score_row`` call gets ``index_df`` + ``index_name`` explicitly —
no default-None wiring on the live path. Worker contract unchanged (DESIGN §6,
685.3): one call reads ONE CSV, scores, releases; workers never hold frames;
the heavy island import resolves in the POOL INITIALIZER, once per worker.
workers=0 runs inline (test seam + degraded hosts; identical code path
through _score_one). Journal output is capped per F9: counts + first 10 error
strings, never the full list.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from multiprocessing import Pool
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io  # noqa: E402
from full_universe import ALLA_CAP, CACHE_DIR, UniversePlan  # noqa: E402
from rocket.technical.regime import REGION_INDEX  # noqa: E402 — one map, B0's

POOL_WORKERS = 8       # 14 cores, ~0.3 GB/worker incl. island (DESIGN §6)
ERROR_LOG_CAP = 10     # F9 journal cap

# Placeholder name for uncovered regions (the 11 without a REGION_INDEX
# entry): NEVER consumed — detect_regime is unreachable without a frame
# (rocket_score.py:285), so an uncovered region scores index_df=None and
# keeps the honest CHOP×0.5 fallback (D1; B0's index_for_region law).
_NO_INDEX_NAME = REGION_INDEX.get("usa", "^GSPC")

_WORKER_INDEX: dict[str, tuple] = {}   # filled once per worker by _init_worker


def _load_indices(cache_dir: Path | None) -> dict[str, tuple]:
    """C1: read the ≤4 REGION_INDEX index CSVs ONCE per run (parent side).

    {region: (index_df|None, index_name)}. detect_regime consumes the
    TitleCase shape, so the stored lowercase frame is converted exactly as
    the per-ticker path does (store frame -> to_indicator_frame). A missing
    or empty CSV is None — the tested fallback, not the wiring."""
    from rocket.backtest.indicator_eval import to_indicator_frame
    out: dict[str, tuple] = {}
    for region, idx_name in REGION_INDEX.items():
        raw = store_io.read_store(idx_name, region, cache_dir=cache_dir)
        if raw is None or raw.empty:
            out[region] = (None, idx_name)
        else:
            out[region] = (to_indicator_frame(raw), idx_name)
    return out


def _init_worker(indices: dict[str, tuple] | None = None) -> None:
    """Pool initializer: the heavy island import resolves once per worker and
    the once-per-run index frames ride initargs (one pickle per worker, never
    one per task)."""
    import nightly_score  # noqa: F401 — island chain bound into this worker
    global _WORKER_INDEX
    _WORKER_INDEX = indices or {}


def _score_one(task: tuple[str, str, str, bool]) -> dict:
    """Picklable module-level worker: (ticker, region, cache_dir_str, social).

    meta = {ticker, region, row|None, last_bar, has_usable_store, error}.
    Reads one CSV, scores, releases. Never raises across the pool boundary.
    ``social`` is EXPLICIT on the task tuple (D2: no hidden default anywhere
    on the nightly path); the index pair is explicit on every score_row call
    (C1) — an unmapped region resolves (None, placeholder) = CHOP×0.5.
    """
    ticker, region, cache_dir_str, social = task
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
        import nightly_score
        index_df, index_name = _WORKER_INDEX.get(
            region, (None, _NO_INDEX_NAME))
        meta["row"] = nightly_score.score_row(
            df, ticker, region, social=social,
            index_df=index_df, index_name=index_name)
    except Exception as exc:  # noqa: BLE001 — one ticker must not kill the run
        meta["error"] = f"{ticker}: {exc}"
    return meta


def _run_tasks(tasks: list[tuple], *, workers: int,
               indices: dict[str, tuple]) -> list[dict]:
    """One pass of the driver over picklable tasks (Pool or inline)."""
    if not tasks:
        return []
    if workers and workers > 0:
        with Pool(workers, initializer=_init_worker,
                  initargs=(indices,)) as pool:
            return pool.map(_score_one, tasks, chunksize=20)
    _init_worker(indices)
    return [_score_one(t) for t in tasks]


def score_all(plan: UniversePlan, *, cache_dir: Path | None = None,
              workers: int = POOL_WORKERS) -> dict:
    """Score every planned ticker from the store, two passes (D2); returns::

        {"results": {region: [row dicts]},        # demo_render shape (unchanged)
         "meta":    {ticker: meta dict},          # manifest + §4 partition
         "last_bars": {region: [ISO date]},       # per-region, §4 manifest
         "scalars": {...}}

    No frames ever leave a worker. ``attempted`` counts unique planned
    tickers (pass-2 re-scoring is not a second attempt).
    """
    cdir = str(cache_dir or CACHE_DIR)
    indices = _load_indices(cache_dir)                     # once per run, C1
    tasks = [(t, region, cdir, False) for region in plan.order
             for t in plan.regions[region]]
    metas1 = _run_tasks(tasks, workers=workers, indices=indices)   # pass 1

    top = sorted((m for m in metas1 if m["row"] is not None),
                 key=lambda m: m["row"]["overall"], reverse=True)[:ALLA_CAP]
    top_keys = {m["ticker"] for m in top}
    tasks2 = [(t, region, cdir, True) for (t, region, _, _) in tasks
              if t in top_keys]
    metas2 = _run_tasks(tasks2, workers=workers, indices=indices)  # pass 2

    # C3 merge rule — by ticker, BEFORE results/partition (PLAN-c3 §6 B2):
    merged: dict[str, dict] = {m["ticker"]: m for m in metas1}
    for m2 in metas2:
        m1 = merged.get(m2["ticker"])
        if m2["row"] is not None:
            merged[m2["ticker"]] = m2          # replace ONLY when it carries a row
        elif m1 is not None and m2["error"]:
            m1["error"] = m2["error"]          # pass-1 row KEPT; error rides meta
    metas = [merged[m["ticker"]] for m in metas1]   # stable order, no doubles

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
