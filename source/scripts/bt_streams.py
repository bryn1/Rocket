"""Stage-A per-(ticker,indicator) signal streams over the UNTOUCHED engine (T-BT3).

For one campaign snapshot (built by bt_snapshot, whose dir owns all outputs):
every store CSV x every registry indicator (rocket_score.INDICATORS —
imported, never re-listed) becomes ``<snapshot>/streams/<TICKER>/<INDICATOR>.
jsonl.gz``: a header doc carrying ``store_snapshot_ts`` (PLAN-c3 §2), then
one line per bar keyed by BAR DATE — ``d`` ISO date, ``s`` signal state,
``o``/``c`` prices, so Stage-B replays trades from the slice alone (§4
physical holdout). Signals come from ``indicator_eval._replay`` IMPORTED
verbatim: the engine is not copied, edited, or re-implemented (the §5
deepcopy-free speedup is a stated campaign-side candidate, no card's turf).
jsonl.gz = stdlib only, the store's own csv/json+gzip idiom — no new deps.

Gate + resumable chunk_state live in bt_chunkgate (one concern per file);
the bar gate + zero-signal ledger semantics in bt_bargate. Pool capped 8
(plan's own cap); deterministic output (sorted keys, gzip mtime=0, workers
re-read their own frame) — only ``streams_cost.json`` (§5 per-indicator
seconds) is non-repeatable and sits outside the manifest. Zero network:
store_io/fixture paths only, no network client imported.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io                                   # noqa: E402 (read seam, §6)
import bt_bargate as bg                           # noqa: E402
import bt_chunkgate as cg                         # noqa: E402
import bt_snapshot                                # noqa: E402 (manifest seam)
from full_universe import CACHE_DIR               # noqa: E402

MAX_WORKERS = 8                                  # §5: campaign's own Pool cap
STREAMS_DIR = "streams"
CHUNK_STATE = "chunk_state.json"
STREAMS_MANIFEST = "streams_manifest.json"
STREAMS_COST = "streams_cost.json"

_REGISTRY: dict | None = None


def _registry() -> dict:
    from rocket.scoring.rocket_score import INDICATORS   # THE registry
    return {type(i).__name__: i for i in INDICATORS}


def _init_worker() -> None:
    """Pool initializer: resolve the registry once per worker (sample_backtest
    idiom; instances are stateless under _replay's per-bar deepcopy)."""
    global _REGISTRY
    _REGISTRY = _registry()


def _dump_lines(path: Path, lines: list[dict]) -> int:
    """gzip mtime=0 + fixed json form => byte-deterministic stream file."""
    blob = "".join(json.dumps(d, sort_keys=True, separators=(",", ":"))
                   + "\n" for d in lines).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.GzipFile(str(tmp), "wb", compresslevel=9, mtime=0) as gz:
        gz.write(blob)
    size = tmp.stat().st_size
    os.replace(tmp, path)
    return size


def _build_unit(task: tuple) -> dict:
    """Picklable worker unit = one ticker, its pending indicators. Re-reads
    its own frame (sample_backtest idiom); returns streamed/excluded result."""
    from rocket.backtest.indicator_eval import (WARMUP_BARS, _replay,
                                                to_indicator_frame)
    ticker, csv_path, out_root, names, ts = task
    try:
        frame = store_io.read_cache(Path(csv_path))
    except Exception:
        return {"ticker": ticker, "kind": "excluded",
                "reason": bg.REASON_UNREADABLE}
    reason = bg.exclusion_reason(frame)
    if reason:
        return {"ticker": ticker, "kind": "excluded", "reason": reason}
    ind_frame = to_indicator_frame(frame)
    dates = [str(d.date()) for d in ind_frame["date"]]
    opens = ind_frame["Open"].to_numpy(float)
    closes = ind_frame["Close"].to_numpy(float)
    signals, cost_s, nbytes = {}, {}, 0
    root = Path(out_root) / ticker
    for name in names:
        t0 = time.perf_counter()
        sig, errors, _meta = _replay(_REGISTRY[name], ind_frame)
        cost_s[name] = round(time.perf_counter() - t0, 3)
        order = sorted(sig)
        states = [sig[t].value for t in order]
        lines = [{"d": dates[t], "s": states[i],
                  "o": float(opens[t]), "c": float(closes[t])}
                 for i, t in enumerate(order)]
        n_sig = bg.transition_count(states)
        lines.insert(0, {"ticker": ticker, "indicator": name,
                         "store_snapshot_ts": ts, "warmup_bars": WARMUP_BARS,
                         "bars_store": len(ind_frame), "bars_stream": len(order),
                         "n_signals": n_sig, "calc_errors": errors})
        nbytes += _dump_lines(root / f"{name}.jsonl.gz", lines)
        signals[name] = n_sig
    return {"ticker": ticker, "kind": "streamed", "names": list(names),
            "signals": signals, "bytes": nbytes, "cost_s": cost_s}


def build_streams(snapshot_dir: Path, store_dir: Path, *,
                  tickers: list[str] | None = None,
                  indicators: list[str] | None = None,
                  workers: int = MAX_WORKERS, resume: bool = False,
                  now: datetime | None = None,
                  checkpoint_every: int = 25) -> dict:
    """Run one chunk; returns the written streams_manifest dict. Refuses
    (GateRefused / ChunkStateError) naming every reason; writes nothing
    outside <snapshot_dir>."""
    snapshot_dir, store_dir = Path(snapshot_dir), Path(store_dir)
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not mpath.exists():
        raise cg.GateRefused([f"no campaign snapshot manifest at {mpath} — "
                              "run bt_snapshot.py build for this store first"])
    snap = json.loads(mpath.read_text(encoding="utf-8"))
    ts = {"run_iso": snap["run_iso"],
          "store_listing_sha256": snap["store_listing_sha256"]}
    cg.chunk_start_gate(snapshot_dir, store_dir,
                        now or datetime.now(timezone.utc))
    names = sorted(_registry())
    if indicators is not None:
        unknown = sorted(set(indicators) - set(names))
        if unknown:
            raise ValueError(f"unknown indicator(s): {unknown}")
        names = [n for n in names if n in indicators]
    stems = sorted(p.stem for p in store_dir.glob("*.csv"))
    if tickers is not None:
        missing = sorted(set(tickers) - set(stems))
        if missing:
            raise ValueError(f"no CSV for ticker(s): {missing}")
        stems = [s for s in stems if s in set(tickers)]
    state_path = snapshot_dir / CHUNK_STATE
    state = cg.load_state(state_path, ts["store_listing_sha256"], resume)
    root = snapshot_dir / STREAMS_DIR
    pending = [(s, str(store_dir / f"{s}.csv"), str(root),
                [n for n in names if n not in
                 set(state["units_done"].get(s, ()))], ts)
               for s in stems]
    pending = [t for t in pending if t[3]]
    global _REGISTRY
    if _REGISTRY is None:
        _init_worker()
    cost_run: dict[str, list[float]] = {}
    if workers == 1 or len(pending) < 2:
        results = [_build_unit(t) for t in pending]
    else:
        with ProcessPoolExecutor(max_workers=min(workers, MAX_WORKERS),
                                 initializer=_init_worker) as pool:
            results = list(pool.map(_build_unit, pending))   # submission order
    for i, res in enumerate(results, start=1):
        if res["kind"] == "excluded":
            state["excluded_done"][res["ticker"]] = res["reason"]
        else:
            state["excluded_done"].pop(res["ticker"], None)
            state["units_done"][res["ticker"]] = sorted(
                set(state["units_done"].get(res["ticker"], []))
                | set(res["names"]))
            state["bytes_written"] += res["bytes"]
            for k, v in res["signals"].items():
                state["signals_by_indicator"][k] = \
                    state["signals_by_indicator"].get(k, 0) + v
            for k, v in res["cost_s"].items():
                cost_run.setdefault(k, []).append(v)
        if i % checkpoint_every == 0:
            cg.save_json(state_path, state)
    cg.save_json(state_path, state)
    streamed_names = sorted({n for done in state["units_done"].values()
                             for n in done})
    signals = {k: state["signals_by_indicator"].get(k, 0)
               for k in streamed_names}
    manifest = {"store_snapshot_ts": ts, **bg.ledger(state["units_done"],
                                                     state["excluded_done"]),
                "indicators_streamed": streamed_names,
                "signals_by_indicator": signals,
                "zero_signal_indicators": bg.zero_signal_indicators(signals),
                "bytes_written": state["bytes_written"]}
    cg.save_json(snapshot_dir / STREAMS_MANIFEST, manifest)
    cg.save_json(snapshot_dir / STREAMS_COST, {
        k: {"n_units": len(v), "seconds_total": round(sum(v), 3),
            "seconds_mean": round(sum(v) / len(v), 3)}
        for k, v in sorted(cost_run.items())})
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-dir", required=True, type=Path,
                    help="campaign snapshot dir (bt_snapshot --out-dir)")
    ap.add_argument("--store", default=str(CACHE_DIR), type=Path)
    ap.add_argument("--tickers", default="", help="CSV stems (default: all)")
    ap.add_argument("--indicators", default="",
                    help="registry class names (default: all 34)")
    ap.add_argument("--workers", default=MAX_WORKERS, type=int)
    ap.add_argument("--resume", action="store_true",
                    help="skip (ticker,indicator) keys already done")
    ap.add_argument("--checkpoint-every", default=25, type=int)
    args = ap.parse_args(argv)
    cg.courtesy()
    try:
        m = build_streams(
            args.snapshot_dir, args.store,
            tickers=[t for t in args.tickers.split(",") if t.strip()] or None,
            indicators=[i for i in args.indicators.split(",") if i.strip()]
            or None,
            workers=args.workers, resume=args.resume,
            checkpoint_every=args.checkpoint_every)
    except (cg.GateRefused, cg.ChunkStateError, ValueError) as exc:
        print(f"REFUSE — {'; '.join(getattr(exc, 'reasons', [str(exc)]))}",
              file=sys.stderr)
        return 1
    print(f"streams OK — {m['tickers_streamed']} streamed, "
          f"{m['tickers_excluded']} excluded, {m['bytes_written']} B written "
          f"-> {args.snapshot_dir / STREAMS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
