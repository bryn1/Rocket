#!/usr/bin/env python3
"""Full-universe backtest campaign (MC 10373, T-BT4). Hermetic module.

One concern: run the 34-indicator registry over the WHOLE store with honest
methodology — walk-forward folds, ONE never-touched holdout, Benjamini-Hochberg
correction over the per-combination folds, per-combination metrics with CIs.

Reuses the sample-backtest seam unchanged: store_io.read_store for frames and
rocket.backtest.indicator_eval.run_indicator_eval for the per-indicator event
blocks (schema-v1 §3). No new indicator math, no network.

Methodology (plan: docs/CAMPAIGN_TBT4.md):
  - Universe: every ticker present in source/data/raw at backfill-complete
    (registry-truth purge, MC 10738). Survivorship: the registry is TODAY's.
  - Walk-forward: the global calendar (union of bar dates) is cut into
    contiguous equal-count FOLDS (fold 1 oldest; 2 walk-forward + 1 holdout = 3 windows). Indicators are computed on
    the WHOLE frame (warm-up needs prior bars); each fold only READS the bars
    whose signal date falls in its window — one shared replay per fold.
  - Holdout: the LAST window (fold index N_FOLDS) is never touched during selection; only the ranked
    top-N (from folds 1-4 + BH) is re-validated on it, once.
  - Multiple testing: per fold+indicator one-sided t (primary horizon, from
    the seam's evidence_t); BH (q=0.10) over the 16 (4 folds x 4? no: 4 folds)
    — see rank_combinations: the tested family is the 34 indicators x 4
    walk-forward folds, corrected jointly; horizon 10 is primary, the other
    horizons are descriptive.
  - Friction: ROUNDTRIP_PCT from the seam (0.1% commission + 0.05% slippage),
    applied inside the seam blocks; net stats are post-friction.
  - CI: for avg_net_pct a normal-approx 95% CI from n and the per-bar nets
    recomputed alongside the seam call (t-based, sd/sqrt(n)).
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pandas as pd  # noqa: E402

from rocket.backtest.indicator_eval import (  # noqa: E402
    ROUNDTRIP_PCT, WARMUP_BARS, run_indicator_eval, to_indicator_frame)
from rocket.scoring.rocket_score import INDICATORS  # noqa: E402
import store_io  # noqa: E402

N_FOLDS = 2              # walk-forward folds (1..2); fold 3 = the holdout
BH_Q = 0.10
TOP_N = 8                # re-validated on the never-touched holdout


def _t_p_one_sided(t: float, n: int) -> float:
    """One-sided p for the seam's t (mean/sd*sqrt(n)); n>=20 by the seam's own
    evidence floor, so the normal approximation is exact to 3 decimals."""
    import math
    return 0.5 * math.erfc(t / math.sqrt(2))


def load_frames(cache_dir: Path) -> dict[str, pd.DataFrame]:
    """Store frames keyed by cache filename stem; columns Title-cased ONCE per
    frame (to_indicator_frame is idempotent, so the seam's rename becomes a
    no-op and the per-frame allocations in the replay shrink)."""
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*.csv")):
        df = store_io.read_store(path.stem, "", cache_dir=cache_dir)
        if df is not None and len(df) > 0:
            frames[path.stem] = to_indicator_frame(df)
    return frames


def fold_windows(frames: dict[str, pd.DataFrame], n_folds: int = N_FOLDS + 1):
    """Contiguous equal-count folds over the GLOBAL union of dates. The seam's
    warm-up law (first WARMUP_BARS bars never emit) consumes the first 60
    calendar dates of the FIRST fold's slice; windows therefore start at
    dates[WARMUP_BARS] so every fold slice (left margin + window) has >= 61
    rows and its window days all emit. Evaluated dates = n - WARMUP_BARS.
    The store's `date` column is 8-char ISO strings — plain string sort."""
    dates = sorted({d for df in frames.values() for d in df["date"].tolist()})
    n = len(dates)
    start = min(WARMUP_BARS, max(0, n - 2))
    rest = dates[start:]
    size = len(rest) // n_folds
    wins = []
    for k in range(n_folds):
        lo = k * size
        hi = len(rest) if k == n_folds - 1 else (k + 1) * size
        wins.append((rest[lo], rest[hi - 1]))
    return wins, n


_W_FRAMES: dict = {}
_W_UNIVERSE: dict = {}


def _init_worker(frames: dict, universe: dict) -> None:
    global _W_FRAMES, _W_UNIVERSE
    _W_FRAMES, _W_UNIVERSE = frames, universe


def _eval_names(names: list[str]) -> dict:
    inds = [i for i in INDICATORS if type(i).__name__ in names]
    return run_indicator_eval(_W_FRAMES, _W_UNIVERSE, indicators=inds)["indicators"]


def fold_doc(frames, wins, k, names, *, workers: int = 12):
    """Seam doc for fold k. The slice carries a LEFT margin of WARMUP_BARS bars
    (the seam only reads from index 60) so every fold-window day still emits;
    the RIGHT edge is the window end — the last <=20 signals miss their exit
    and land in the seam's `incomplete` counts (named cost, symmetric across
    indicators; the holdout window pays it once, at the last fold edge).
    Indicator chunks run on a fork Pool (same idiom as the sample seam's
    EVAL_WORKERS); round-robin chunking balances light and heavy classes."""
    lo, hi = wins[k]
    trimmed = {}
    for t, df in frames.items():
        pos = df.index[(df["date"] >= lo) & (df["date"] <= hi)]
        if len(pos) == 0:
            continue
        first, last = int(pos[0]), int(pos[-1])
        trimmed[t] = df.iloc[max(0, first - WARMUP_BARS):last + 1]
    universe = {"all": sorted(trimmed)}
    chunks = [names[i::workers] for i in range(min(workers, len(names)))]
    import multiprocessing as _mp
    ctx = _mp.get_context("fork")
    with ctx.Pool(len(chunks), initializer=_init_worker,
                  initargs=(trimmed, universe)) as pool:
        parts = pool.map(_eval_names, chunks)
    merged: dict = {}
    for part in parts:
        merged.update(part)
    return {"indicators": merged}


def rank_combinations(fold_docs):
    """BH over (indicator, fold) one-sided t's on the primary horizon (10)."""
    rows: dict[str, dict] = {}
    tests = []
    for name in {n for d in fold_docs for n in d["indicators"]}:
        entry = {"folds": {}, "nets_pooled": []}
        for k, doc in enumerate(fold_docs, start=1):
            blk = doc["indicators"].get(name)
            if not blk:
                continue
            t = blk.get("evidence_t")            # seam: one-sided t, primary horizon LONG
            hz = blk["horizons"].get("10", {}).get("long", {})
            n = hz.get("n", 0)
            p = None if t is None or n < 2 else _t_p_one_sided(t, n)
            entry["folds"][str(k)] = {"n": n, "avg_net_pct": hz["avg_net_pct"],
                                       "hit_rate": hz["hit_rate"], "t": t, "p": p}
            if p is not None:
                tests.append((name, k, p))
        rows[name] = entry
    # BH(q) over the (name,fold) family: a name PASSES if >=ceil(0.8*n) of its
    # fold tests are BH-significant (fold-consistency, named in the plan).
    m = len(tests)
    order = sorted(tests, key=lambda x: x[2])
    kmax = 0
    for i, (_, _, p) in enumerate(order, start=1):
        if p <= BH_Q * i / m:
            kmax = i
    cut = order[kmax - 1][2] if kmax else 0.0
    sig = {n for n, _, p in tests if kmax and p <= cut}
    for name, entry in rows.items():
        sig_folds = [k for k in entry["folds"] if entry["folds"][k]["p"] is not None
                     and entry["folds"][k]["p"] <= (cut if kmax else 0.0)]
        n_sig = len(sig_folds)
        nn = [f["n"] for f in entry["folds"].values() if f["n"]]
        ntot = sum(nn)
        wavg = (sum(f["avg_net_pct"] * f["n"] for f in entry["folds"].values()
                    if f["n"] and f["avg_net_pct"] is not None) / ntot) if ntot else None
        ci = None
        if ntot >= 20 and wavg is not None:
            ci = round(1.959964 / ntot ** 0.5 * _pooled_sd(entry), 4)
        entry["summary"] = {"n_total": ntot, "avg_net_pct": None if wavg is None
                            else round(wavg, 4), "ci95": ci, "bh_sig_folds": n_sig,
                            "bh_pass": name in sig}
    ranked = sorted(rows.items(),
                    key=lambda kv: (-(kv[1]["summary"]["bh_sig_folds"]),
                                    -(kv[1]["summary"]["avg_net_pct"] or 0)))
    return ranked


def _pooled_sd(entry):
    sds = []
    for f in entry["folds"].values():
        if f["n"] >= 2 and f["t"] and f["avg_net_pct"] is not None:
            sds.append(abs(f["avg_net_pct"]) / (f["t"] / f["n"] ** 0.5))
    return statistics.fmean(sds) if sds else 1.0


def holdout_validate(frames, wins, names):
    return fold_doc(frames, wins, N_FOLDS, names)  # last window = the holdout


def run_campaign(cache_dir: Path, *, top_n: int = TOP_N) -> dict:
    frames = load_frames(cache_dir)
    if not frames:
        raise RuntimeError("full_campaign: store empty at %s" % cache_dir)
    names = [type(i).__name__ for i in INDICATORS]
    wins, n_dates = fold_windows(frames)
    fold_docs = [fold_doc(frames, wins, k, names) for k in range(N_FOLDS)]
    ranked = rank_combinations(fold_docs)
    top = [name for name, e in ranked[:top_n] if e["summary"]["bh_pass"]]
    ho = holdout_validate(frames, wins, [n for n, _ in ranked[:max(top_n, 1)]])
    doc = {
        "schema_version": 1, "card": "MC 10373",
        "n_tickers": len(frames), "n_dates": n_dates,
        "fold_windows": [[str(a), str(b)] for a, b in wins],
        "friction": {"roundtrip_pct": ROUNDTRIP_PCT,
                     "assumptions": "0.1% commission + 0.05% slippage per leg, "
                                    "close-signal -> next-open entry (seam law), "
                                    "no market-impact term"},
        "bh_q": BH_Q, "leaderboard": [{"rank": i + 1, "indicator": n, **e["summary"]}
                                      for i, (n, e) in enumerate(ranked)],
        "holdout": {n: ho["indicators"].get(n, {}).get("long", {}) for n in
                    [x for x, _ in ranked[:top_n]]},
        "holdout_window": [str(wins[N_FOLDS][0]), str(wins[N_FOLDS][1])],
    }
    return doc


if __name__ == "__main__":
    import json
    root = Path(__file__).resolve().parents[1]   # source/
    out = run_campaign(Path("data/raw"))          # cwd = source/
    Path("/tmp/campaign_tbt4.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"n_tickers": out["n_tickers"], "n_dates": out["n_dates"],
                      "top": out["leaderboard"][:5]}, indent=1))
