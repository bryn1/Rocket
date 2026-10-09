"""bt_combo (T-BT4): the pinned anti-drift PARITY test and the load-bearing
holdout assertion, each with its planted RED. PARITY: >=3 fixture300 tickers
x >=2 indicators — grid gross == the engine's OWN emissions, captured by
WRAPPING indicator_eval._block during an untouched _eval_indicator run over
the FULL store frame (engine emission order). RED pair: a planted off-by-one
entry shift breaks that same comparison; a hand-verified trade (dates+prices
+net) pins the port literally. OPEN-EVERY-FILE (DA-P2-2): a full-span streams
file copied into slices/trainval/ makes the grid REFUSE, naming file + bar
date + check. Plus: sha re-assert red, singles+pairs+triples enumeration,
pinned unanimous confluence, byte-determinism, per-fold n_trials_path
growth."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_combo as gc                               # noqa: E402
import bt_ds                                        # noqa: E402
import bt_slice                                     # noqa: E402
import bt_snapshot as bs                            # noqa: E402
from bt_fixture_select import FIXTURE_DIR           # noqa: E402
from bt_fold_fixtures import LIGHT, make_store, ready_snapshot   # noqa: E402
from rocket.backtest import indicator_eval as ie    # noqa: E402
from rocket.scoring.rocket_score import INDICATORS  # noqa: E402


def full_span_bars(snap: Path, tkr: str, ind: str) -> dict:
    _h, bars = bt_slice.read_stream(snap / "streams" / tkr / f"{ind}.jsonl.gz")
    return {"dates": [b["d"] for b in bars], "states": [b["s"] for b in bars],
            "opens": [b["o"] for b in bars], "closes": [b["c"] for b in bars]}


def engine_gross(ind, csv_path: Path):
    """The ENGINE's own _eval_indicator over the FULL store frame (streams
    start post-WARMUP — the engine must see the frame it saw); ie._block is
    WRAPPED (never replaced semantically) to capture the raw gross lists in
    engine emission order [(5,long),(5,short),(10,long),...]."""
    import store_io
    frame = ie.to_indicator_frame(store_io.read_cache(csv_path))
    prepared = {"T": (frame, frame["Open"].to_numpy(float),
                      frame["Close"].to_numpy(float))}
    seen: list[list[float]] = []
    orig = ie._block

    def spy(gross):
        seen.append(list(gross))
        return orig(gross)

    ie._block = spy
    try:
        block, incomplete = ie._eval_indicator(ind, prepared, (5, 10, 20), 10,
                                               max(0, len(frame) - 60))
    finally:
        ie._block = orig
    return block, incomplete, seen


def fixture_store(tmp: Path, n: int = 3) -> tuple[Path, list[str]]:
    fat = sorted(p.stem for p in FIXTURE_DIR.glob("*.csv")
                 if len(p.read_text(encoding="utf-8").splitlines()) > 71)
    sel = fat[:n]
    store = tmp / "store"
    store.mkdir(parents=True, exist_ok=True)
    for s in sel:                                  # byte-lock kept: copy
        (store / f"{s}.csv").write_bytes((FIXTURE_DIR / f"{s}.csv").read_bytes())
    return store, sel


def synth_ready(tmp: Path) -> tuple[Path, Path]:
    store = make_store(tmp, ["AA", "BB", "CC"])
    return store, ready_snapshot(tmp, store, ["AA", "BB", "CC"])


# ── the VERBATIM port, hand-verified EXACT (dates + prices + net) ───────────

def test_replay_rule_hand_verified_exact_trades():
    n = 10
    bars = {"dates": [f"2026-01-{10 + i:02d}" for i in range(n)],
            "states": ["HOLD", "BUY", "HOLD", "HOLD", "HOLD", "HOLD",
                       "SELL", "HOLD", "HOLD", "HOLD"],
            "opens": [100.0 + i for i in range(n)],
            "closes": [101.0 + i for i in range(n)]}
    trades, inc_l, inc_s, nbuy, nsell = gc.replay_trades(bars, (5, 10, 20))
    longs = [t for t in trades if t["side"] == "long"]
    assert len(longs) == 3 and {t["horizon"] for t in longs} == {5, 10, 20}
    for t in longs:                        # SELL@6 is first opposite for all h
        assert (t["entry_date"], t["exit_date"]) == ("2026-01-12", "2026-01-16")
        assert (t["entry_px"], t["exit_px"]) == (102.0, 107.0)
        assert t["gross_pct"] == pytest.approx(5.0 / 102.0 * 100)
        assert t["net_pct"] == pytest.approx(5.0 / 102.0 * 100 - 0.3)
    # SELL@6: entry 7, no opposite BUY remains — every horizon exits past the
    # data -> 3 incomplete shorts (engine counting :146-147), opens[entry]
    # never indexed back:
    assert (inc_l, inc_s, nbuy, nsell) == (0, 3, 1, 1)
    assert not [t for t in trades if t["side"] == "short"]


# ── PINNED PARITY + its planted RED ──────────────────────────────────────────

def _grid_seen(trades):
    return [[t["gross_pct"] for t in trades if t["horizon"] == h
             and t["side"] == side]
            for h in (5, 10, 20) for side in ("long", "short")]


def test_grid_trade_rule_parity_vs_engine_exact(tmp_path):
    store, tickers = fixture_store(tmp_path)
    snap = ready_snapshot(tmp_path, store, tickers)
    registry = {type(i).__name__: i for i in INDICATORS}
    total_trades = 0
    for tkr in tickers:
        for ind_name in LIGHT:
            bars = full_span_bars(snap, tkr, ind_name)
            block, incomplete, eng = engine_gross(registry[ind_name],
                                                  store / f"{tkr}.csv")
            trades, inc_l, inc_s, nbuy, nsell = gc.replay_trades(bars)
            assert len(eng) == 6
            for e, g in zip(eng, _grid_seen(trades)):
                assert e == g                      # exact floats, same order
            assert (nbuy, nsell) == (block["n_buy"], block["n_sell"])
            assert {"long": inc_l, "short": inc_s} == incomplete
            total_trades += len(trades)
    assert total_trades >= 12                      # NOT vacuous: real trades


def test_grid_parity_red_catches_planted_off_by_one_entry(tmp_path):
    """RED pair: shift the signal series by ONE bar (entry-alignment drift)
    -> the SAME comparison goes RED. Mutation lives in this test only."""
    store, tickers = fixture_store(tmp_path)
    snap = ready_snapshot(tmp_path, store, tickers)
    registry = {type(i).__name__: i for i in INDICATORS}
    caught = 0
    for tkr in tickers:
        bars = full_span_bars(snap, tkr, "RSI")
        _b, _i, eng = engine_gross(registry["RSI"], store / f"{tkr}.csv")
        clean, *_ = gc.replay_trades(bars)
        mut, *_ = gc.replay_trades(dict(bars, states=bars["states"][1:]
                                         + ["HOLD"]))
        if (_grid_seen(clean) == [e for e in eng]
                and _grid_seen(mut) != [e for e in eng]):
            caught += 1
    assert caught >= 2              # RED proven on >=2 of the 3 tickers


# ── DA-P2-2 OPEN-EVERY-FILE: full-span file copied into trainval -> RED ────

def test_open_every_file_red_full_span_into_trainval(tmp_path):
    store, snap = synth_ready(tmp_path)
    src = snap / "streams" / "AA" / "RSI.jsonl.gz"             # FULL span
    dst = snap / "slices" / "trainval" / "AA" / "XFAKE.jsonl.gz"
    shutil.copyfile(src, dst)                                  # the PLANT
    with pytest.raises(gc.GridRefused) as exc:
        gc.run_pass(snap, "trainval", store)
    reasons = " ; ".join(exc.value.reasons)
    assert "OPEN-EVERY-FILE" in reasons              # the named check
    assert "XFAKE" in reasons and "outside" in reasons
    c2 = json.loads(bs.manifest_path(snap).read_text(
        encoding="utf-8"))["cuts"]["C2"]
    assert c2 in reasons                             # names the bar date
    assert not (snap / "grid" / "trainval" / "trials.json").exists()


def test_open_root_green_without_the_plant(tmp_path):   # GREEN pair
    store, snap = synth_ready(tmp_path)
    doc = gc.run_pass(snap, "trainval", store, LIGHT)
    assert doc["fold"] == "trainval" and doc["trials"]


def test_store_sha_reasserted_every_pass(tmp_path):
    store, snap = synth_ready(tmp_path)
    p = store / "AA.csv"                             # nightly-style growth
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(gc.GridRefused) as exc:
        gc.run_pass(snap, "val1", store, LIGHT)
    assert "store listing sha" in " ; ".join(exc.value.reasons)


# ── grid geometry + pinned combo semantics ─────────────────────────────────

def test_enumerate_combos_is_singles_pairs_triples():
    assert gc.enumerate_combos(["A"]) == [("A",)]
    assert gc.enumerate_combos(["A", "B", "C"]) == [
        ("A",), ("B",), ("C",), ("A", "B"), ("A", "C"), ("B", "C"),
        ("A", "B", "C")]


def test_combo_states_is_unanimous_confluence():
    got = gc.combo_states([["BUY", "HOLD", "SELL", "BUY"],
                           ["BUY", "BUY", "SELL", "HOLD"]])
    assert got == ["BUY", "HOLD", "SELL", "HOLD"]


def test_zero_signal_levers_never_padded_into_combos(tmp_path):
    store, snap = synth_ready(tmp_path)              # §3: no padding
    doc = gc.run_pass(snap, "trainval", store, LIGHT)
    assert doc["levers"] == LIGHT
    assert not set(doc["levers"]) & ie.RISK_ONLY_CLASSES
    with pytest.raises(gc.GridRefused) as exc:       # dead lever: refused
        gc.run_pass(snap, "trainval", store, ["MatchFinder"])
    assert "not gridable" in " ; ".join(exc.value.reasons)


# ── manifest: n_trials_path grows per fold, read-modify-write ───────────────

def test_n_trials_path_accumulates_per_fold(tmp_path):
    store, snap = synth_ready(tmp_path)
    d1 = gc.run_pass(snap, "trainval", store, LIGHT)
    n_combos = d1["n_combos_this_pass"]              # 2 singles + 1 pair
    assert (d1["n_trials_path_before"], d1["n_trials_path_after"]) == \
        (0, {"trainval": n_combos, "val1": 0, "val2": 0})
    d2 = gc.run_pass(snap, "trainval", store, LIGHT)             # pass ADDS
    assert d2["n_trials_path_after"]["trainval"] == 2 * n_combos
    d3 = gc.run_pass(snap, "val1", store, LIGHT)
    assert d3["n_trials_path_after"] == {"trainval": 2 * n_combos,
                                         "val1": n_combos, "val2": 0}
    man = json.loads(bs.manifest_path(snap).read_text(encoding="utf-8"))
    assert man["n_trials_path"] == d3["n_trials_path_after"]


def test_trials_json_deterministic_and_gate_shaped(tmp_path):
    store, snap = synth_ready(tmp_path)
    gc.run_pass(snap, "trainval", store, LIGHT)
    first = (snap / "grid" / "trainval" / "trials.json").read_bytes()
    twin_store = make_store(tmp_path / "twin", ["AA", "BB", "CC"])
    twin = ready_snapshot(tmp_path / "twin", twin_store, ["AA", "BB", "CC"])
    gc.run_pass(twin, "trainval", twin_store, LIGHT)
    assert (twin / "grid" / "trainval" / "trials.json").read_bytes() == first
    trials = json.loads(first.decode("utf-8"))["trials"]
    for t in trials:                               # bt_ds row contract:
        assert {"combo", "sr", "gamma3", "gamma4"} <= set(t)
        assert t["sr"] is None or t["evaluable"]   # dead: missing, not 0
    with pytest.raises(bt_ds.NoEdgeGuard) as exc:  # 3 combos < 30 evaluable
        bt_ds.fold_dsr_table(trials, 30, 65)       # -> honest guard, named
    assert "evaluable" in str(exc.value)


def test_grid_cli_refusal_and_green_exit_codes(tmp_path):
    store, snap = synth_ready(tmp_path)
    env = {**os.environ, "BT_OVERRIDE_LOAD": "1"}
    ok = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_combo.py"),
         "--snapshot-dir", str(snap), "--fold", "trainval",
         "--store", str(store), "--indicators", "RSI"],
        capture_output=True, text=True, timeout=300, env=env)
    assert ok.returncode == 0 and "grid trainval" in ok.stdout, ok.stderr
    bad = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_combo.py"),
         "--snapshot-dir", str(tmp_path / "nosnap"), "--fold", "val1",
         "--store", str(store)],
        capture_output=True, text=True, timeout=300, env=env)
    assert bad.returncode == 1 and "REFUSE" in bad.stderr
