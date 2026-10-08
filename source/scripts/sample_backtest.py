"""Weekly Indikatorer redraw: deterministic sample + chunked eval + merge.

DESIGN §8. Three pieces, each independently testable:

1. ``stratified_sample`` — PURE deterministic stratified sampler, no RNG
   (nothing to seed; same input twice -> identical output, pinned by test):
   per non-empty region quota_r = max(1, round(total * n_r / n_live)), fixed
   stride over the region's SORTED live list, tail-trim to quota, and a final
   hard cap at ``total``.
2. Indicator split — the 34 registered indicators sorted by class name into
   12 chunks; ``_eval_chunk`` workers RE-READ their sample frames from the CSV
   store (no frame pickling across the pool, no accumulation) and call
   ``run_indicator_eval(..., indicators=chunk)`` (kwarg exists at
   indicator_eval.py:169-173, engine untouched).
3. ``merge_chunk_docs`` — union of ``indicators`` (re-sorted) + summed
   ``incomplete_dropped``; equality asserted over EXACTLY the bounded key list
   ``MERGE_ASSERT_KEYS`` (``generated_at`` and ``engine`` differ per chunk by
   construction — asserting them would red every Saturday, VERIFIED §8;
   cycle-1's blanket assert is retired). ``generated_at`` set at merge;
   ``engine`` derived from the merged doc, never the literal 34 (C2-F6.3).

The embedded ``universe`` field is the SAMPLE's universe (<= sample size,
DESIGN §4 page contract), and the additive ``sample`` block rides the doc.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io  # noqa: E402
from full_universe import CACHE_DIR, UniversePlan  # noqa: E402

SAMPLE_SIZE = 500       # §8: fits the owner's September-era capped product
CHUNKS = 12             # Pool(12) around run_indicator_eval (DESIGN §2/§8)
EVAL_WORKERS = CHUNKS

# Bounded merge assertion (DESIGN §8): equality across chunks over EXACTLY
# these keys. generated_at/engine are NOT assertable by construction.
MERGE_ASSERT_KEYS = ("schema_version", "data_last_bar", "costs_pct",
                     "horizons_days", "primary_horizon_days", "entry_rule")


class SampleBacktestError(RuntimeError):
    """Chunk docs disagree on an assertable field, or merge input is empty."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stratified_sample(live_by_region: dict[str, list[str]],
                      total: int = SAMPLE_SIZE) -> dict[str, list[str]]:
    """Deterministic stratified sample (pure function, no RNG).

    Returns {region: [sampled tickers (input order kept, selection by fixed
    stride over the region's sorted list)]}, iterating regions in the given
    dict order. Empty regions contribute nothing; every non-empty region is
    present with >= 1 member; the flat selection is capped at ``total``.
    """
    n_live = sum(len(v) for v in live_by_region.values())
    if n_live == 0:
        return {}
    quotas = {r: max(1, round(total * len(v) / n_live))
              for r, v in live_by_region.items() if v}
    while sum(quotas.values()) > total:  # hard cap: shave largest quotas first
        top = max(quotas, key=lambda r: (quotas[r], r))
        if quotas[top] > 1:
            quotas[top] -= 1
        else:
            del quotas[top]  # pathological many-tiny-regions case: the hard
            # cap wins over full region coverage (never happens at 19 regions)
    out: dict[str, list[str]] = {}
    for region, tickers in live_by_region.items():
        quota = quotas.get(region, 0)
        if quota <= 0:
            continue
        ordered = sorted(tickers)
        stride = max(1, len(ordered) // quota)
        picked = ordered[::stride][:quota]
        keep = set(picked)
        out[region] = [t for t in tickers if t in keep]
    return out


def sample_size(sampled: dict[str, list[str]]) -> int:
    return sum(len(v) for v in sampled.values())


def sample_block(n: int) -> dict:
    """Additive indicator_stats.json sample block (DESIGN §4 page contract)."""
    return {"tickers": n, "method": "deterministic-stratified",
            "cadence": "weekly"}


def indicator_chunks(n_chunks: int = CHUNKS) -> list[list[str]]:
    """The registered indicators, sorted by class name, split into <=
    n_chunks contiguous deterministic chunks; returns CLASS NAMES so tasks
    stay picklable."""
    from rocket.scoring.rocket_score import INDICATORS
    names = sorted(type(i).__name__ for i in INDICATORS)
    n_chunks = max(1, min(n_chunks, len(names)))
    base, extra = divmod(len(names), n_chunks)
    chunks, at = [], 0
    for i in range(n_chunks):
        size = base + (1 if i < extra else 0)
        chunks.append(names[at:at + size])
        at += size
    return [c for c in chunks if c]


def _init_worker() -> None:
    """Pool initializer: resolve the indicator registry once per worker."""
    from rocket.backtest import indicator_eval  # noqa: F401
    from rocket.scoring.rocket_score import INDICATORS
    globals()["_IND_BY_NAME"] = {type(i).__name__: i for i in INDICATORS}


def _eval_chunk(task: tuple) -> dict:
    """Picklable worker: (chunk_names, [(ticker, region)], cache_dir_str) ->
    one chunk doc. Re-reads its sample frames from the store (DESIGN §6:
    backtest workers re-read their sample frames only)."""
    chunk_names, members, cache_dir_str = task
    frames: dict[str, pd.DataFrame] = {}
    universe: dict[str, list[str]] = {}
    for ticker, region in members:
        df = store_io.read_store(ticker, region, cache_dir=Path(cache_dir_str))
        if df is not None and len(df) > 0:
            frames[ticker] = df
            universe.setdefault(region, []).append(ticker)
    if not frames:
        raise SampleBacktestError("sample chunk has no readable frames")
    from rocket.backtest.indicator_eval import run_indicator_eval
    inds = [_IND_BY_NAME[n] for n in chunk_names]
    return run_indicator_eval(frames, universe, indicators=inds)


def merge_chunk_docs(docs: list[dict], *, sample_size_n: int,
                     now_fn=_utc_stamp) -> dict:
    """Bounded-equality merge (§8). Raises SampleBacktestError naming the
    offending key on any disagreement in MERGE_ASSERT_KEYS."""
    if not docs:
        raise SampleBacktestError("merge: no chunk docs")
    ref = docs[0]
    for key in MERGE_ASSERT_KEYS:
        for i, d in enumerate(docs[1:], start=1):
            if d.get(key) != ref.get(key):
                raise SampleBacktestError(
                    f"merge: chunk {i} disagrees on asserted field '{key}': "
                    f"{d.get(key)!r} != {ref.get(key)!r}")
    merged = dict(ref)
    merged["generated_at"] = now_fn()
    indicators: dict[str, dict] = {}
    for d in sorted(docs, key=lambda d: sorted(d["indicators"])):
        indicators.update(d["indicators"])
    merged["indicators"] = {k: indicators[k] for k in sorted(indicators)}
    dropped = {"long": 0, "short": 0}
    for d in docs:
        for side in dropped:
            dropped[side] += d.get("incomplete_dropped", {}).get(side, 0)
    merged["incomplete_dropped"] = dropped
    from rocket.backtest.indicator_eval import WARMUP_BARS
    merged["engine"] = {"indicators_registered": len(merged["indicators"]),
                        "tickers": sample_size_n, "warmup_bars": WARMUP_BARS}
    merged["sample"] = sample_block(sample_size_n)
    return merged


def run_sample_backtest(plan: UniversePlan, *, cache_dir: Path | None = None,
                        workers: int = EVAL_WORKERS,
                        sample_size_total: int = SAMPLE_SIZE,
                        live_by_region: dict[str, list[str]] | None = None,
                        eval_fn=None, now_fn=_utc_stamp) -> dict:
    """Weekly redraw entry: sample -> chunked eval -> merged schema-v1 doc.

    ``live_by_region`` lets the orchestrator pass the STORE-LIVE tickers
    (frames must exist to evaluate); default = the whole plan. ``eval_fn`` /
    ``workers=0`` are test seams (inline single-process, real eval).
    """
    live = live_by_region if live_by_region is not None else plan.regions
    sampled = {r: v for r, v in stratified_sample(live, sample_size_total)
               .items() if v}
    n = sample_size(sampled)
    if n == 0:
        raise SampleBacktestError("sample is empty — nothing to evaluate")
    members = [(t, r) for r, ts in sampled.items() for t in ts]
    universe = {r: sorted(ts) for r, ts in sampled.items()}
    chunks = indicator_chunks(workers if workers else CHUNKS)
    cdir = str(cache_dir or CACHE_DIR)
    if eval_fn is not None:
        docs = [eval_fn(c, members, cdir) for c in chunks]
    elif workers and workers > 0:
        with Pool(workers, initializer=_init_worker) as pool:
            docs = pool.map(_eval_chunk, [(c, members, cdir) for c in chunks])
    else:
        _init_worker()
        docs = [_eval_chunk((c, members, cdir)) for c in chunks]
    doc = merge_chunk_docs(docs, sample_size_n=n, now_fn=now_fn)
    doc["universe"] = universe  # the SAMPLE's universe, never the full one
    return doc
