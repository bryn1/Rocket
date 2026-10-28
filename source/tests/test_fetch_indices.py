"""B0 (MC 11545): regime index feed — PLAN-c3 §6 B0 row, hermetic (fake
fetcher through the existing stage-1 seam, no network, RUN-RELATIVE bars):
(a) dry-run fetch lands <=4 index CSVs (the REGION_INDEX tickers);
(b) detect_regime returns NON-CHOP on the landed CSVs (non-CHOP path
    exercised end-to-end: fetch -> store -> read -> regime);
(c) planted-bad: empty CSV -> honest CHOP×0.5 fallback, no crash;
(d) NAMED: a region with NO REGION_INDEX entry keeps ×0.5 without crash
    (the D1 honest fallback — index_for_region -> None, never a "^GSPC"
    default for the 11 uncovered regions);
(e) honest failure: a dead fetch leaves the store byte-unchanged and
    logs — never a partial CSV;
(f) idempotent: re-running the same fetch lands byte-identical CSVs.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import fetch_indices                                      # noqa: E402
import fetch_store as fetch_stage                         # noqa: E402
import full_universe as fu                                # noqa: E402
import store_io                                           # noqa: E402
import rocket.data.bulk_fetcher as bf                     # noqa: E402
from demo_page_fixtures import NOW                        # noqa: E402
from rocket.backtest.indicator_eval import to_indicator_frame  # noqa: E402
from rocket.technical.regime import (                     # noqa: E402
    REGION_INDEX, Regime, detect_regime, index_for_region)

TREND_BARS = 260          # > 200 so BOTH EMAs are defined (else BEAR arm)


def _trend_frame(bars=TREND_BARS):
    """Deterministic uptrend: linear close, tight stable range -> DI+ only,
    DX≈100, price > EMA50/EMA200 -> the BULL arm of detect_regime."""
    close = [100.0 + 0.5 * i for i in range(bars)]
    return pd.DataFrame({
        "date": [NOW.date() - timedelta(days=bars - 1 - i)
                 for i in range(bars)],
        "open": close, "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close], "close": close,
        "volume": [1_000_000.0] * bars})


def _stage(fetcher):
    """The REAL stage-1 walk with an injected fake fetcher (same seam
    production binds when fetcher=None)."""
    def _fn(plan, **kw):
        return fetch_stage.run_fetch(plan, fetcher=fetcher, **kw)
    return _fn


def _ok_fetcher(batch, period):
    return {t: _trend_frame() for t in batch}, {}


def _dead_fetcher(batch, period):
    raise RuntimeError("simulated Yahoo death")


def _fetch(tmp_path, monkeypatch, fetcher=_ok_fetcher, **run_kw):
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    kw = dict(skip_fetch=False, now_fn=lambda: NOW, cache_dir=tmp_path)
    kw.update(run_kw)
    return fetch_indices.run(_stage(fetcher), **kw)


def test_dry_run_fetch_lands_index_csvs(tmp_path, monkeypatch):
    facts = _fetch(tmp_path, monkeypatch)
    assert 0 < len(facts) <= 4                       # the D1 <=4 law
    assert set(facts) == set(REGION_INDEX.values())
    assert all(f["batch_completed"] for f in facts.values())
    for ticker in facts:
        path = tmp_path / fu.cache_filename(ticker)
        assert path.exists(), path                   # landed, one per index
        stored = store_io.read_store(ticker, "usa", cache_dir=tmp_path)
        assert stored is not None and len(stored) >= 200


def test_detect_regime_non_chop_on_landed_indices(tmp_path, monkeypatch):
    _fetch(tmp_path, monkeypatch)
    for region, ticker in REGION_INDEX.items():
        stored = store_io.read_store(ticker, region, cache_dir=tmp_path)
        assert stored is not None and not stored.empty
        res = detect_regime(to_indicator_frame(stored), ticker)
        assert res.regime is not Regime.CHOP         # damper replaced
        assert res.multiplier > 0.5


def test_planted_bad_empty_csv_falls_back_to_chop_half(tmp_path):
    (tmp_path / fu.cache_filename(REGION_INDEX["usa"])).write_text("")
    stored = store_io.read_store(REGION_INDEX["usa"], "usa",
                                 cache_dir=tmp_path)
    assert stored is None                            # tolerant reader heals
    res = detect_regime(stored)                      # the scorer's path
    assert res.regime is Regime.CHOP
    assert res.multiplier == 0.5                     # honest, never a crash


def test_unmapped_region_keeps_half_fallback():
    # D1 honest fallback: the uncovered regions resolve to None — never the
    # "^GSPC" default — so the caller passes index_df=None and ×0.5 stands.
    assert index_for_region("usa") == REGION_INDEX["usa"]
    for region in ("germany", "france", "japan", "korea"):
        assert region not in REGION_INDEX
        assert index_for_region(region) is None
    res = detect_regime(None)                        # what B2 supplies
    assert res.regime is Regime.CHOP
    assert res.multiplier == 0.5


def test_failed_fetch_leaves_store_unchanged_and_logs(tmp_path, monkeypatch):
    prior = tmp_path / fu.cache_filename(REGION_INDEX["usa"])
    prior.write_bytes(b"date,open,high,low,close,volume\n")  # existing bytes
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    errors: list[str] = []
    facts = _fetch(tmp_path, monkeypatch, fetcher=_dead_fetcher,
                   errors=errors)
    assert errors                                    # loud, rides the list
    assert all(not f["batch_completed"] for f in facts.values())
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    # ^ never a partial CSV: the store is byte-identical after the death


def test_refetch_is_idempotent(tmp_path, monkeypatch):
    _fetch(tmp_path, monkeypatch)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert len(first) == len(REGION_INDEX)
    _fetch(tmp_path, monkeypatch)                    # same frames again
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == first
