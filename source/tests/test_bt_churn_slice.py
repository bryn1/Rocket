"""churn_slice (T-BT5.1): the §4(iv) DESCRIPTIVE slice. Pins — partition
classes re-derived through store_io.classify_accounting itself (first match
wins; scored / insufficient_rows / dead_at_fetch land exactly on streamed /
bar-gated / unreadable-and-absent, not_fetched structurally empty on a
pinned batch), per-(combo, class) turnover + the SIGN only, snapshot ts on
the artifact, re-runnable byte-identical (it opens nothing one-shot). The
vocabulary ban ships with its own teeth: the scan finds planted tokens, so
its silence on the real artifact means something. Refusals (no manifest /
no grid pass / sha drift / unknown combo) each name their reason."""
from __future__ import annotations

import json
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
import bt_snapshot as bs                            # noqa: E402
import churn_slice as cs                            # noqa: E402
import store_io                                     # noqa: E402
from bt_fold_fixtures import (LIGHT, make_store, ready_snapshot)  # noqa: E402

BAD = ("gate", "pass", "fail", "verdict", "leaderboard", "no-edge")


def ready_grid(tmp, tickers=("AA", "BB", "CC")):
    store = make_store(tmp, list(tickers))
    snap = ready_snapshot(tmp, store, list(tickers))
    gc.run_pass(snap, "val2", store, LIGHT)
    return store, snap


def scan_vocab(text: str) -> list[str]:
    low = text.lower()
    return [t for t in BAD if t in low]


# ── partition classes through store_io's OWN classifier ─────────────────────

def test_partition_classes_first_match_exact(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])   # AA,BB streamed
    (store / "THIN.csv").write_text("date,open,high,low,close,volume\n"
                                    "2026-10-08,1,1,1,1,1\n", encoding="utf-8")
    (store / "BROKEN.csv").write_bytes(b"\x00\x01\x02not a frame")
    part = cs.partition_store(store, snap, members={"AA", "ZZZ_NOFILE"})
    assert part["AA"] == (store_io.SCORED, True)             # streamed, member
    assert part["BB"] == (store_io.SCORED, False)           # streamed orphan
    assert part["THIN"] == (store_io.INSUFFICIENT_ROWS, False)   # bar-gated
    assert part["BROKEN"] == (store_io.DEAD_AT_FETCH, False)     # unparseable
    assert part["ZZZ_NOFILE"] == (store_io.DEAD_AT_FETCH, True)  # no file
    assert store_io.NOT_FETCHED not in {c for c, _ in part.values()}


# ── GREEN: artifact shape, signs, snapshot ts, vocabulary ban ───────────────

def test_churn_artifact_is_descriptive_turnover_and_signs(tmp_path):
    store, snap = ready_grid(tmp_path)
    doc = cs.run_churn(snap, store, "val2", None, 0, members={"AA"})
    man = json.loads(bs.manifest_path(snap).read_text(encoding="utf-8"))
    assert doc["descriptive_only"] is True
    assert doc["store_snapshot_ts"] == {"run_iso": man["run_iso"],
                                        "store_listing_sha256":
                                            man["store_listing_sha256"]}
    assert doc["registry_ts"] == man["registry_ts"]
    trials = json.loads((snap / "grid/val2/trials.json").read_text(
        encoding="utf-8"))["trials"]
    assert [c["combo"] for c in doc["combos"]] == \
        [t["combo"] for t in trials]              # every trial of the pass
    scored_total = 0
    for combo in doc["combos"]:
        assert list(combo["by_class"]) == list(cs.CLASSES)   # all four, kept
        for cls, row in combo["by_class"].items():
            assert {"tickers", "churn_tickers", "n_trades_long_h10",
                    "churn_n_trades_long_h10", "in_position_fraction",
                    "trades_per_ticker_year", "sign", "churn_sign"} <= set(row)
            assert row["churn_n_trades_long_h10"] <= row["n_trades_long_h10"]
            assert (row["sign"] is None) == (row["n_trades_long_h10"] == 0)
            assert row["sign"] in (None, "positive", "negative", "zero")
            scored_total += row["n_trades_long_h10"]
    assert scored_total > 0                       # NOT vacuous: real trades
    assert doc["universe"]["class_counts"] == {  # everything streamed here:
        store_io.SCORED: 3, store_io.NOT_FETCHED: 0,
        store_io.INSUFFICIENT_ROWS: 0, store_io.DEAD_AT_FETCH: 0}
    churn_cell = doc["combos"][0]["by_class"][store_io.SCORED]
    assert churn_cell["tickers"] == 3 and churn_cell["churn_tickers"] == 2
    text = json.dumps(doc)
    assert not scan_vocab(text)


def test_vocab_scan_has_teeth(tmp_path):
    """RED pair for the ban above: the SAME scan FINDS planted tokens — a
    silent scan would prove nothing."""
    assert scan_vocab('{"x": "the GATE cleared, tests pass, nothing FAILED,'
                      ' verdict NO-EDGE, LEADERBOARD promoted"}') \
        == list(BAD)
    assert scan_vocab("clean descriptive turnover, sign only") == []


def test_churn_rerun_byte_identical(tmp_path):
    """No one-shot marker here (the slice opens nothing irrecoverable): a
    second run is byte-identical."""
    store, snap = ready_grid(tmp_path)
    cs.run_churn(snap, store, "val2", None, 0, members={"AA"})
    first = (snap / cs.CHURN_DIR / "val2" / cs.CHURN_FILE).read_bytes()
    cs.run_churn(snap, store, "val2", None, 0, members={"AA"})
    assert (snap / cs.CHURN_DIR / "val2" / cs.CHURN_FILE).read_bytes() == first


# ── filters: --combos subset + --top ranking ────────────────────────────────

def test_combo_filter_and_top_ranking(tmp_path):
    store, snap = ready_grid(tmp_path)
    doc = cs.run_churn(snap, store, "val2", ["RSI"], 0, members={"AA"})
    assert [c["combo"] for c in doc["combos"]] == ["RSI"]
    trials = json.loads((snap / "grid/val2/trials.json").read_text(
        encoding="utf-8"))["trials"]
    best = sorted((t for t in trials if t.get("sr") is not None),
                  key=lambda t: (-t["sr"], t["combo"]))[0]["combo"]
    top1 = cs.run_churn(snap, store, "val2", None, 1, members={"AA"})
    assert [c["combo"] for c in top1["combos"]] == [best]


# ── refusals name their clause, nothing half-written ────────────────────────

def test_churn_refusals_are_named(tmp_path):
    store = make_store(tmp_path, ["AA"])
    with pytest.raises(cs.ChurnRefused) as exc:
        cs.run_churn(tmp_path / "nosnap", store, "val2")
    assert "no campaign snapshot manifest" in " ; ".join(exc.value.reasons)

    snap = ready_snapshot(tmp_path, store, ["AA"])
    with pytest.raises(cs.ChurnRefused) as exc:          # no grid pass yet
        cs.run_churn(snap, store, "val2", None, 0, members=set())
    assert "no grid doc" in " ; ".join(exc.value.reasons)
    gc.run_pass(snap, "val2", store, LIGHT)
    with pytest.raises(cs.ChurnRefused) as exc:          # combo not in pass
        cs.run_churn(snap, store, "val2", ["NOSUCH+LEVER"], 0, members=set())
    assert "absent from" in " ; ".join(exc.value.reasons)

    p = store / "AA.csv"                                 # nightly-style drift
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    man = json.loads(bs.manifest_path(snap).read_text(encoding="utf-8"))
    with pytest.raises(cs.ChurnRefused) as exc:
        cs.run_churn(snap, store, "val2", None, 0, members=set())
    r = " ; ".join(exc.value.reasons)
    assert "store listing sha" in r
    assert man["store_listing_sha256"] in r              # both shas named
    assert not (snap / cs.CHURN_DIR).exists()            # nothing half-written


def test_cli_green_and_refusal_exit_codes(tmp_path):
    store, snap = ready_grid(tmp_path)
    ok = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "churn_slice.py"),
         "--snapshot-dir", str(snap), "--store", str(store),
         "--fold", "val2", "--top", "1"],
        capture_output=True, text=True, timeout=600)
    assert ok.returncode == 0 and "churn val2" in ok.stdout, ok.stderr
    assert not scan_vocab(ok.stdout)                    # descriptive stdout
    bad = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "churn_slice.py"),
         "--snapshot-dir", str(tmp_path / "nosnap"), "--store", str(store)],
        capture_output=True, text=True, timeout=300)
    assert bad.returncode == 1 and "REFUSE" in bad.stderr


# ── --snapshot-attested (MC 10390 owner ruling 2026-10-20 16:59Z, Option A) ──

def test_attested_churn_survives_store_drift(tmp_path):
    """Mirror of the grid clause: the scan still counts the universe, it
    only stops GATING; the strict default keeps refusing the drift."""
    store, snap = ready_grid(tmp_path)
    p = store / "AA.csv"                             # nightly-style growth
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(cs.ChurnRefused):             # STRICT default bites
        cs.run_churn(snap, store, "val2")
    doc = cs.run_churn(snap, store, "val2", snapshot_attested=True)
    assert doc["attestation"].startswith("ATTESTED") and "MC 10390" in \
        doc["attestation"]
    assert doc["combos"] and not scan_vocab(
        json.dumps(doc["combos"]))                   # descriptive holds
