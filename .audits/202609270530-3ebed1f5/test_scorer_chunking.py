"""Regression test: chunked HistoricalStore scoring == legacy whole-frame scoring.

Builds a tiny synthetic store (per-date parquet files, the real store's layout),
scores it through BOTH paths of 685.3 (legacy DataFrame groupby and the chunked
HistoricalStore) and asserts byte-identical score rows. Guards the OOM fix
(MC 1361-followup) against semantic drift in the streaming path.
"""
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

OUT_DIR = Path(__file__).resolve().parent
REF_PATH = Path("/srv/workspace/svarkor-rocket-25k-data-collect/teddy/685.3-score-top25-export-20260826.py")


def _load_ref():
    spec = importlib.util.spec_from_file_location("ref6853", REF_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ref6853"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ref():
    return _load_ref()


@pytest.fixture(scope="module")
def tiny_store(tmp_path_factory):
    """12 dates x 6 tickers, ragged on purpose: one 1-obs ticker, one 0-obs."""
    root = tmp_path_factory.mktemp("tinystore")
    hist = root / "historical"
    hist.mkdir()
    tickers = ["AAA.NS", "BBB.AX", "CCC.DE", "DDD.USA", "EEE.USA", "FFF.USA"]
    regions = ["india", "australia", "germany", "usa", "usa", "usa"]
    start = date(2026, 8, 1)
    for i in range(12):
        rows = []
        for t, reg in zip(tickers, regions):
            # FFF gets a single observation on the first date only (insufficient);
            # EEE starts on date 3 (ragged start).
            if t == "FFF.USA" and i > 0:
                continue
            if t == "EEE.USA" and i < 3:
                continue
            rows.append({
                "ticker": t, "region": reg, "date": start + timedelta(days=i),
                "open": 10.0 + i, "high": 11.0 + i, "low": 9.0 + i,
                "close": 10.0 + i + (0.5 if t == "AAA.NS" else 0.0),
                "volume": 1000 * (i + 1), "source_symbol": None if t == "CCC.DE" else t,
                "adj_factor": 1.0,
            })
        pq.write_table(pa.Table.from_pylist(rows), hist / f"historical_2026-08-{i+1:02d}.parquet")
    return root


def test_legacy_and_chunked_paths_agree(ref, tiny_store):
    legacy_frame = pd.concat(
        [pq.read_table(f).to_pandas() for f in sorted(tiny_store.glob("historical/*.parquet"))],
        ignore_index=True,
    )
    legacy = ref.build_scores_df(legacy_frame)
    store = ref.HistoricalStore(tiny_store, batch_files=3)  # forces 4 batches
    chunked = ref.build_scores_df(store)
    pd.testing.assert_frame_equal(legacy, chunked, check_dtype=True)


def test_store_metadata_surface(ref, tiny_store):
    store = ref.HistoricalStore(tiny_store)
    n_rows = sum(pq.read_metadata(f).num_rows for f in tiny_store.glob("historical/*.parquet"))
    assert len(store) == n_rows
    assert store["ticker"].nunique() == 6
    assert store.ticker.nunique() == 6


def test_ragged_tickers_scored_not_crashed(ref, tiny_store):
    store = ref.HistoricalStore(tiny_store, batch_files=2)
    scores = ref.build_scores_df(store)
    fff = scores[scores["ticker"] == "FFF.USA"].iloc[0]
    assert fff["reason"] == "insufficient_data" and fff["score"] == 0.0
    assert int(scores["reason"].eq("scored").sum()) == 5
