"""Synthetic weekday store + snapshot/stream/slice fixtures shared by the
fold tests (T-BT4). FAT_DATES clears every §4 fold floor on today's geometry;
make_store writes deterministic deterministic-price CSVs — the same bytes
always, so two runs of two stores compare byte-for-byte."""
from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path


def weekdays(a: str, b: str) -> list[str]:
    out, d = [], date.fromisoformat(a)
    while d <= date.fromisoformat(b):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


FAT_DATES = weekdays("2025-11-03", "2026-10-08")   # clears every §4 floor
RUN_ISO = "2026-10-09T00:00:00Z"
LIGHT = ["MACD", "RSI"]


def make_store(root: Path, tickers: list[str]) -> Path:
    import math
    store = root / "store"
    store.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(tickers):
        rows = []
        for j, d in enumerate(FAT_DATES):
            px = 10 + i + math.sin((j + i * 7) / 5.0) * 0.5
            rows.append(f"{d},{px:.4f},{px + 0.6:.4f},{px - 0.6:.4f},"
                        f"{px + 0.2:.4f},{(i + 1) * 100}")
        (store / f"{name}.csv").write_text(
            "date,open,high,low,close,volume\n" + "\n".join(rows) + "\n",
            encoding="utf-8")
    return store


def gz_lines(path: Path) -> list[dict]:
    with gzip.open(path, "rb") as f:
        return [json.loads(line) for line in f]


def ready_snapshot(tmp: Path, store: Path, tickers: list[str]) -> Path:
    """Store -> snapshot -> streams -> slices (the full T-BT2..4 chain)."""
    import bt_slice
    import bt_snapshot as bs
    import bt_streams as st
    snap = tmp / "snap"
    bs.build_snapshot(store, snap, run_iso=RUN_ISO)
    st.build_streams(snap, store, tickers=tickers, indicators=LIGHT,
                     workers=1)
    bt_slice.materialize_slices(snap)
    return snap
