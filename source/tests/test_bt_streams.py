"""bt_streams Stage-A: gate, bar-gate ledger, determinism, resume (T-BT3).

Every guard ships its planted-RED proof next to the GREEN one (T16 idiom):
the chunk-start gate is fed a drifted store at a fake 03:00 clock (must
REFUSE naming both clauses) and at 07:01 (must pass via the window); the
bar gate is pinned at exactly WARMUP, WARMUP+H-1, WARMUP+H bars. Byte-level
claims use whole-tree byte maps: two runs are byte-identical, and a
kill-mid-run simulation (run AA -> resume BB+CC) lands on the SAME bytes as
one single-shot pass — streams_cost.json (wall-clock timing) is excluded
from byte comparison BY DESIGN and is the only non-repeatable artifact. The
zero-signal ledger must balance: every covered ticker is streamed OR
excluded, no third state. Hermetic: synthetic weekday stores + 3 CSVs
copied out of fixture300 (incl. one 1-bar THIN file — the fixture's thin
bucket IS the exclusion test); a socket-boom subprocess run proves the
import path and the build touch no network.
"""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_bargate as bg                 # noqa: E402
import bt_chunkgate as cg               # noqa: E402
import bt_snapshot as bs                # noqa: E402
import bt_streams as st                 # noqa: E402
from bt_fixture_select import FIXTURE_DIR   # noqa: E402

RUN_ISO = "2026-10-09T00:00:00Z"
F0300 = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)   # window closed
F0701 = datetime(2026, 10, 9, 7, 1, tzinfo=timezone.utc)   # window open
LIGHT = ["RSI", "MACD"]                # cheap registry subset for pairs


def weekdays(a: str, b: str) -> list[str]:
    out, d = [], date.fromisoformat(a)
    while d <= date.fromisoformat(b):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


FAT_DATES = weekdays("2025-11-03", "2026-10-08")   # clears every §4 floor


def make_store(root: Path, tickers: list[str]) -> Path:
    store = root / "store"
    store.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(tickers):
        body = "".join(f"{d},{i + 10.0},{i + 11.0},{i + 9.0},"
                       f"{i + 10.5 + (j % 5) * 0.1},{(i + 1) * 100}\n"
                       for j, d in enumerate(FAT_DATES))
        (store / f"{name}.csv").write_text(
            "date,open,high,low,close,volume\n" + body, encoding="utf-8")
    return store


def green_snapshot(tmp: Path, tickers=("AA", "BB", "CC")) -> tuple[Path, Path]:
    store = make_store(tmp, list(tickers))
    bs.build_snapshot(store, tmp / "snap", run_iso=RUN_ISO)
    return store, tmp / "snap"


def gz_lines(path: Path) -> list[dict]:
    with gzip.open(path, "rb") as f:
        return [json.loads(line) for line in f]


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Snapshot-dir tree minus the one non-deterministic artifact (§5 cost)."""
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != st.STREAMS_COST}


def thin_csv(path: Path) -> Path:
    path.write_text("date,open,high,low,close,volume\n"
                    "2026-10-08,1,1,1,1,1\n", encoding="utf-8")
    return path


# ── bar gate: the §3 floor pinned at the boundary (pure, no IO) ─────────────

def _frame(n_bars: int, dated: bool = True) -> pd.DataFrame:
    cols = {"open": [1.0] * n_bars, "close": [1.0] * n_bars}
    if dated:
        cols["date"] = pd.date_range("2026-01-01", periods=n_bars)
    return pd.DataFrame(cols)


def test_bar_gate_exact_boundaries():
    """< WARMUP+H excluded, >= streamed: exactly WARMUP and WARMUP+H-1 are
    out; WARMUP+H is the first bar-count with a complete primary-horizon
    window. A 1-bar fixture-style file is the thin bucket."""
    assert bg.WARMUP_BARS == 60 and bg.H_PRIMARY == 10
    assert bg.exclusion_reason(_frame(bg.WARMUP_BARS)) == bg.REASON_BARS
    assert bg.exclusion_reason(_frame(bg.MIN_BARS - 1)) == bg.REASON_BARS
    assert bg.exclusion_reason(_frame(bg.MIN_BARS)) is None
    assert bg.exclusion_reason(_frame(1)) == bg.REASON_BARS
    assert bg.exclusion_reason(_frame(80, dated=False)) == bg.REASON_NO_DATE
    assert bg.exclusion_reason(None) == bg.REASON_UNREADABLE


def test_transition_count_is_engine_edge_trigger_semantics():
    # BUY counts once on entry, not per bar; HOLD never counts.
    assert bg.transition_count(
        ["HOLD", "BUY", "BUY", "SELL", "HOLD", "SELL"]) == 3
    assert bg.transition_count(["HOLD"] * 9) == 0


# ── chunk-start gate: RED at 03:00 on a drifted store, GREEN at 07:01 ───────

def test_gate_green_matching_store_at_night(tmp_path):
    store, snap = green_snapshot(tmp_path)
    assert cg.chunk_start_gate(snap, store, F0300) == \
        {"mode": "store-matches-snapshot"}


def test_gate_red_drift_before_window(tmp_path):
    """Planted RED: nightly-style growth of one CSV after the snapshot —
    03:00 clock must REFUSE naming the sha drift AND the closed window."""
    store, snap = green_snapshot(tmp_path)
    p = store / "AA.csv"
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(cg.GateRefused) as exc:
        cg.chunk_start_gate(snap, store, F0300)
    reasons = " ; ".join(exc.value.reasons)
    assert "listing sha" in reasons and "drifted" in reasons
    assert "courtesy window" in reasons and "07:00" in reasons


def test_gate_green_drift_after_window_opens(tmp_path):
    store, snap = green_snapshot(tmp_path)
    p = store / "AA.csv"
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    assert cg.chunk_start_gate(snap, store, F0701)["mode"] == "courtesy-window"


def test_gate_refuses_missing_snapshot_named(tmp_path):
    store = make_store(tmp_path, ["AA"])
    with pytest.raises(cg.GateRefused) as exc:
        st.build_streams(tmp_path / "nosnap", store, tickers=["AA"],
                         indicators=LIGHT, workers=1, now=F0701)
    assert "no campaign snapshot manifest" in " ; ".join(exc.value.reasons)


def test_gate_missing_nightly_manifest_is_not_green(tmp_path):
    """§2: an absent manifest is NOT the green branch — only the 07:00
    window can carry a chunk there (the gate reads whatever manifest exists;
    no manifest file at all is refused by the named check above)."""
    store, snap = green_snapshot(tmp_path)
    (snap / ".tmp" / "backtest_manifest.json").unlink()
    assert bs.validate_snapshot(store, snap)          # drift is non-empty
    with pytest.raises(cg.GateRefused):
        cg.chunk_start_gate(snap, store, F0300)       # RED: not green + night


# ── smoke on fixture300: 2 fat + 1 THIN over the FULL 34-indicator registry ─

def _fixture_store(tmp: Path) -> tuple[Path, list[str], str]:
    fat = sorted(p.stem for p in FIXTURE_DIR.glob("*.csv")
                 if len(p.read_text(encoding="utf-8").splitlines()) > 71)
    thin = sorted(p.stem for p in FIXTURE_DIR.glob("*.csv")
                  if len(p.read_text(encoding="utf-8").splitlines()) < 71)
    store = tmp / "store"
    store.mkdir(parents=True, exist_ok=True)
    sel = fat[:2] + thin[:1]
    for s in sel:                                     # byte-lock kept: copy
        (store / f"{s}.csv").write_bytes((FIXTURE_DIR / f"{s}.csv").read_bytes())
    return store, fat[:2], thin[0]


def test_smoke_fixture300_streams_and_ledger(tmp_path):
    """Full registry (34) over 2 fat fixture tickers + 1 THIN (the fixture's
    thin bucket): thin EXCLUDED and counted in the ledger, every stream doc
    carries the snapshot's store_snapshot_ts, bars keyed by date from the
    warmup bar on, per-indicator cost measured, MatchFinder (empty-reference
    no-op, §1) in the zero-signal set."""
    store, fat, thin = _fixture_store(tmp_path)
    bs.build_snapshot(store, tmp_path / "snap", run_iso=RUN_ISO)
    snap_doc = json.loads(bs.manifest_path(tmp_path / "snap")
                          .read_text(encoding="utf-8"))
    m = st.build_streams(tmp_path / "snap", store, workers=2, now=F0300)
    assert m["tickers_streamed"] == 2 and m["tickers_covered"] == 3
    assert m["tickers_excluded"] == {bg.REASON_BARS: 1}
    assert m["ledger_balanced"] is True
    assert len(m["indicators_streamed"]) == 34
    assert m["signals_by_indicator"]["MatchFinder"] == 0
    assert "MatchFinder" in m["zero_signal_indicators"]
    files = sorted((tmp_path / "snap" / "streams").rglob("*.jsonl.gz"))
    assert len(files) == 68                            # 2 tickers x 34 levers
    assert not (tmp_path / "snap" / "streams" / thin).exists()
    totals: dict[str, int] = {}
    for f in files:
        header, *bars = gz_lines(f)
        assert header["store_snapshot_ts"] == {
            "run_iso": snap_doc["run_iso"],
            "store_listing_sha256": snap_doc["store_listing_sha256"]}
        assert header["bars_stream"] == header["bars_store"] - bg.WARMUP_BARS
        assert header["n_signals"] >= 0
        assert all({"d", "s", "o", "c"} <= set(b) for b in bars)
        totals[header["indicator"]] = totals.get(header["indicator"], 0) \
            + header["n_signals"]
    assert totals == m["signals_by_indicator"]   # ledger = docs, exactly
    assert files[0].parent.name in fat                 # dirs = TICKER
    # KEYED BY BAR DATE, pinned: stream line 1 is the store's WARMUP-th bar
    warmup_row = (store / f"{fat[0]}.csv").read_text(
        encoding="utf-8").splitlines()[bg.WARMUP_BARS + 1]
    first_bar = gz_lines(next(f for f in files if f.parent.name == fat[0]
                              and f.name == "RSI.jsonl.gz"))[1]
    assert first_bar["d"] == warmup_row.split(",")[0]
    cost = json.loads((tmp_path / "snap" / st.STREAMS_COST)
                      .read_text(encoding="utf-8"))
    assert len(cost) == 34 and all(c["seconds_total"] > 0
                                   for c in cost.values())      # §5 measured


# ── zero-signal ledger balances against the input count, exactly ────────────

def test_ledger_counts_sum_to_input_count(tmp_path):
    store = make_store(tmp_path, ["AA", "BB", "CC"])
    thin_csv(store / "THIN.csv")                 # in the store BEFORE snapshot
    bs.build_snapshot(store, tmp_path / "snap", run_iso=RUN_ISO)
    m = st.build_streams(tmp_path / "snap", store, indicators=LIGHT,
                         workers=1, now=F0300)
    assert m["tickers_covered"] == 4
    assert m["tickers_streamed"] + sum(m["tickers_excluded"].values()) == 4
    state = json.loads((tmp_path / "snap" / st.CHUNK_STATE)
                       .read_text(encoding="utf-8"))
    assert len(state["units_done"]) + len(state["excluded_done"]) == 4
    assert state["excluded_done"] == {"THIN": bg.REASON_BARS}


# ── determinism: two full runs, byte-identical everything but the cost file ─

def test_two_runs_byte_identical(tmp_path):
    store, snap = green_snapshot(tmp_path, ["AA", "BB"])
    st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300)
    first = tree_bytes(snap)
    assert (snap / ".tmp" / "backtest_manifest.json").exists()   # untouched
    for p in list((snap / "streams").rglob("*")) + [
            snap / st.CHUNK_STATE, snap / st.STREAMS_MANIFEST,
            snap / st.STREAMS_COST]:                   # artifacts only, NOT
        if p.is_file():                                # the frozen snapshot
            p.unlink()
    for d in sorted((snap / "streams").rglob("*"), reverse=True):
        if d.is_dir():
            d.rmdir()
    st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300)
    assert tree_bytes(snap) == first


# ── resume: kill-mid-run simulation completes byte-identical to single-shot ─

def test_resume_pair_byte_identical_and_skips_done(tmp_path):
    tickers = ["AA", "BB", "CC"]
    store, snap = green_snapshot(tmp_path, tickers)
    twin = make_store(tmp_path / "twin", tickers)     # identical data, twin
    bs.build_snapshot(twin, tmp_path / "single", run_iso=RUN_ISO)
    st.build_streams(tmp_path / "single", twin, indicators=LIGHT, workers=1,
                     now=F0300)
    # chunked twin: "killed" after AA finished -> resume finishes BB, CC
    st.build_streams(snap, store, tickers=["AA"], indicators=LIGHT,
                     workers=1, now=F0300)
    m = st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300,
                         resume=True)
    assert m["tickers_streamed"] == 3
    cost = json.loads((snap / st.STREAMS_COST).read_text(encoding="utf-8"))
    # the RESUMED run measured exactly 2 units — AA was skipped, not redone
    assert all(c["n_units"] == 2 for c in cost.values())
    assert tree_bytes(snap) == tree_bytes(tmp_path / "single")


def test_resume_without_flag_refuses_named(tmp_path):
    store, snap = green_snapshot(tmp_path, ["AA"])
    st.build_streams(snap, store, tickers=["AA"], indicators=LIGHT,
                     workers=1, now=F0300)
    with pytest.raises(cg.ChunkStateError) as exc:
        st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300)
    assert "--resume" in str(exc.value)


def test_corrupt_chunk_state_refuses_named_not_silent_restart(tmp_path):
    store, snap = green_snapshot(tmp_path, ["AA"])
    st.build_streams(snap, store, tickers=["AA"], indicators=LIGHT,
                     workers=1, now=F0300)
    (snap / st.CHUNK_STATE).write_text("{trunc", encoding="utf-8")
    with pytest.raises(cg.ChunkStateError) as exc:
        st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300,
                         resume=True)
    assert "corrupt/incompatible" in str(exc.value)
    assert "delete" in str(exc.value)                # NAMED recovery
    # a valid state from a DIFFERENT store state (foreign sha) is refused too
    (snap / st.CHUNK_STATE).unlink()                 # documented recovery path
    st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300)
    doc = json.loads((snap / st.CHUNK_STATE).read_text(encoding="utf-8"))
    doc["store_listing_sha256"] = "0" * 64
    (snap / st.CHUNK_STATE).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(cg.ChunkStateError):
        st.build_streams(snap, store, indicators=LIGHT, workers=1, now=F0300,
                         resume=True)


# ── pool path == inline path (ProcessPool determinism at the byte level) ────

def test_pool_and_inline_agree_byte_for_byte(tmp_path):
    names = [f"T{i}" for i in range(6)]
    store, snap = green_snapshot(tmp_path, names)
    st.build_streams(snap, store, indicators=["MACD"], workers=1, now=F0300)
    inline = tree_bytes(snap / "streams")
    assert len(inline) == 6
    for p in (snap / "streams").rglob("*.jsonl.gz"):
        p.unlink()
    (snap / st.CHUNK_STATE).unlink()      # same cold-start as the inline run
    st.build_streams(snap, store, indicators=["MACD"], workers=4, now=F0300)
    assert tree_bytes(snap / "streams") == inline


# ── courtesy: warn, never fail (permission-denied hosts stay runnable) ──────

def test_courtesy_warns_and_never_raises(monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError("nope")
    monkeypatch.setattr(cg.os, "nice", boom)
    monkeypatch.setattr(cg.subprocess, "run", boom)
    cg.courtesy()                                    # must not raise
    err = capsys.readouterr().err
    assert "warn: nice(19) refused" in err
    assert "warn: ionice idle refused" in err


# ── zero network: socket-boom prelude around the FULL import + build path ───

_SOCKET_PRELUDE = """
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, {source!r}); sys.path.insert(0, {scripts!r})
import socket
class _Boom(socket.socket):          # any socket INSTANTIATION detonates;
    def __init__(self, *a, **k):     # ssl etc. can still IMPORT (they
        raise AssertionError(        # subclass socket.socket at import)
            "network attempt: socket used during bt_streams run")
def _boom_fn(*a, **k):
    raise AssertionError("network attempt: socket name resolution/connect")
socket.socket = _Boom
socket.create_connection = socket.getaddrinfo = _boom_fn
import bt_snapshot as bs, bt_streams as st
dates, d = [], datetime(2025, 11, 3).date()
end = datetime(2026, 10, 8).date()
while d <= end:
    if d.weekday() < 5:
        dates.append(d.isoformat())
    d += timedelta(days=1)
root = Path({root!r})
store = root / "store"
store.mkdir(parents=True)
for i, name in enumerate(("AA", "BB")):
    body = "".join(f"{{dd}},{{i + 10.0}},{{i + 11.0}},{{i + 9.0}},{{i + 10.5}},100\\n"
                   for dd in dates)
    (store / f"{{name}}.csv").write_text("date,open,high,low,close,volume\\n" + body)
bs.build_snapshot(store, root / "snap", run_iso="2026-10-09T00:00:00Z")
m = st.build_streams(root / "snap", store, indicators=["RSI"], workers=1,
                     now=datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc))
assert m["tickers_streamed"] == 2, m
print("SOCKET-BOOM-OK")
"""


def test_zero_network_socket_boom(tmp_path):
    """Import path AND the 2-ticker build run under a socket replacement that
    detonates on ANY use: a clean exit proves neither bt_streams nor the
    engine/registry/store_io import chain touches the network."""
    code = _SOCKET_PRELUDE.format(source=str(SOURCE),
                                  scripts=str(SOURCE / "scripts"),
                                  root=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(SOURCE),
                          capture_output=True, text=True, timeout=300)
    assert "SOCKET-BOOM-OK" in proc.stdout, proc.stderr[-1500:]


# ── CLI: green run exits 0; a refused chunk exits 1 with the named reason ───

def test_cli_green_and_refuse_exit_codes(tmp_path):
    store, snap = green_snapshot(tmp_path, ["AA", "BB"])

    def run_cli(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SOURCE / "scripts" / "bt_streams.py"),
             "--snapshot-dir", str(snap), "--store", str(store),
             "--indicators", "MACD", "--workers", "1", *extra],
            capture_output=True, text=True, timeout=300)

    ok = run_cli("--tickers", "AA")
    assert ok.returncode == 0 and "streams OK" in ok.stdout, ok.stderr
    bad = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_streams.py"),
         "--snapshot-dir", str(tmp_path / "nosnap"), "--store", str(store)],
        capture_output=True, text=True, timeout=300)
    assert bad.returncode == 1 and "REFUSE" in bad.stderr
    # second CLI run over the same snapshot refuses WITHOUT --resume (exit 1)
    dup = run_cli("--tickers", "AA")
    assert dup.returncode == 1 and "REFUSE" in dup.stderr
    again = run_cli("--tickers", "AA", "--resume")
    assert again.returncode == 0                      # skip-completed path
