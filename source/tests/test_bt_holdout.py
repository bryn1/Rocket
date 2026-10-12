"""bt_holdout (T-BT5.1): the one-shot ceremony, base behaviour. GREEN pins —
only bars > C3 materialize (none <= C2, re-read open-every-file), member-
indicator-only root, artifact carries the snapshot ts and stays descriptive
(no result vocabulary), n_trials_path untouched (holdout is no selection
event), slice_files == what is physically present. Every refusal clause
ships its planted FLIP (source mutation in the helper harness only):
marker, doc-existence, top-K cap, sha equality, manifest-key check —
flipped, the ceremony proceeds (or dies unnamed), so the refusal is proven
load-bearing. CLI exit codes ride at the end. The FIX-CYCLE-1 additions
(the two post-materialize guards proven red, --doc non-empty, gate-aware
ranking + --combos, malformed-row refusal, the start purge) live in
test_bt_holdout_fc1.py; shared fixtures in bt_holdout_helpers.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_combo as gc
import bt_holdout as bh
from bt_fold_fixtures import LIGHT, gz_lines, make_store, ready_snapshot
from bt_holdout_helpers import (frozen_doc, grid_ids, holdout_dates,
                                holdout_root, manifest_of, mutated_holdout,
                                physical_files, ready_grid, run_cli,
                                scan_vocab, write_manifest)


# ── GREEN: what the ceremony writes ─────────────────────────────────────────

def test_holdout_materializes_only_bars_past_c3(tmp_path):
    store, snap = ready_grid(tmp_path)
    art = bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                          ["MACD", "RSI"])
    cuts = manifest_of(snap)["cuts"]
    for tkr in ("AA", "BB", "CC"):
        for ind in LIGHT:
            ds = holdout_dates(snap, tkr, ind)
            assert ds and min(ds) > cuts["C3"]           # > C3 …
            assert not [d for d in ds if d <= cuts["C2"]]  # … never <= C2
    hdr = gz_lines(holdout_root(snap) / "AA" / "RSI.jsonl.gz")[0]
    assert hdr["slice"] == "holdout" and hdr["cuts"]["C3"] == cuts["C3"]
    assert hdr["store_snapshot_ts"]["store_listing_sha256"] == \
        manifest_of(snap)["store_listing_sha256"]
    assert art["descriptive_only"] and art["slice_files"] == 6
    assert art["slice_files"] == len(physical_files(snap))   # == physical
    assert art["ranking_source"]["mode"] == "explicit"
    assert art["ranking_source"]["ids"] == ["MACD", "RSI"]   # order kept
    assert art["top_k"] == 2
    ts = art["store_snapshot_ts"]
    assert set(ts) == {"run_iso", "store_listing_sha256"} and ts["run_iso"]
    assert ts["store_listing_sha256"] == \
        manifest_of(snap)["store_listing_sha256"]
    for row in art["combos"]:                            # DESCRIPTIVE rows:
        assert {"daily_series", "long_h10"} <= set(row)
        assert row["daily_series"]["n_days"] == cuts["fold_day_counts"]["holdout"]
    assert [r["combo"] for r in art["combos"]] == ["MACD", "RSI"]
    text = json.dumps(art)
    assert not scan_vocab(text) and not scan_vocab(      # stdout line form:
        f"holdout opened once: {art['slice_files']} files")


def test_only_member_indicators_of_top_k_open(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    gc.run_pass(snap, "val2", store, ["RSI"])            # combos: RSI only
    bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2", ["RSI"])
    assert (holdout_root(snap) / "AA" / "RSI.jsonl.gz").exists()
    assert not (holdout_root(snap) / "AA" / "MACD.jsonl.gz").exists()


def test_holdout_is_not_a_selection_event(tmp_path):
    """§4: the single holdout validation is not a selection event — the
    running n_trials_path must come back byte-identical but the marker."""
    store, snap = ready_grid(tmp_path)
    before = manifest_of(snap)
    bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                    grid_ids(snap))
    after = manifest_of(snap)
    assert after["n_trials_path"] == before["n_trials_path"]
    assert after["holdout_opened"] is True


# ── REFUSALS + planted flips (one per clause -> that refusal is load-bearing)

def test_second_run_refuses_naming_the_marker(tmp_path):
    store, snap = ready_grid(tmp_path)
    doc = frozen_doc(tmp_path)
    bh.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    r = " ; ".join(exc.value.reasons)
    assert "holdout_opened" in r and "spent" in r
    assert physical_files(snap)                        # spent root never purged


def test_marker_flip_makes_the_second_run_proceed(tmp_path):
    """RED pair: disable ONLY the marker clause in a mutated copy -> run #2
    succeeds; the refusal above was the marker check, nothing else."""
    store, snap = ready_grid(tmp_path)
    doc = frozen_doc(tmp_path)
    bh.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    mod = mutated_holdout(('if manifest.get("holdout_opened"):',
                           'if manifest.get("holdout_opened__MUT"):'))
    art = mod.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    assert art["descriptive_only"]                       # ceremony re-ran


@pytest.mark.parametrize("bad_k", [0, 11])
def test_top_k_cap_refuses_naming_k(tmp_path, bad_k):
    store, snap = ready_grid(tmp_path)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), bad_k, "val2")
    r = " ; ".join(exc.value.reasons)
    assert f"top-K = {bad_k}" in r and "10" in r
    assert not holdout_root(snap).exists()
    assert not (snap / bh.HOLDOUT_METRICS).exists()


def test_top_k_cap_flip_lets_eleven_through(tmp_path):
    """RED pair: widen ONLY the cap (gate leg also zeroed so the SAME
    fixture-scale data can rank at all — the gate itself has its own pairs
    in the fc1 file) -> K=11 is accepted and the ceremony completes; the
    refusal above was the cap clause alone."""
    store, snap = ready_grid(tmp_path)
    mod = mutated_holdout(("if not 1 <= top_k <= MAX_TOP_K:",
                           "if not 1 <= top_k <= 10 ** 6:"),
                          ("GATE_MIN_TRADES = 400", "GATE_MIN_TRADES = 0"))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 11, "val2")
    assert art["descriptive_only"]


def test_stale_store_sha_refuses_naming_both_shas(tmp_path):
    store, snap = ready_grid(tmp_path)
    p = store / "AA.csv"                                 # nightly-style drift
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    manifest = manifest_of(snap)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")
    r = " ; ".join(exc.value.reasons)
    assert manifest["store_listing_sha256"] in r         # snapshot sha named
    import bt_snapshot as _bs                            # re-derived named:
    listing, _ = _bs.scan_store(store)
    assert _bs.listing_sha256(listing) in r
    assert not holdout_root(snap).exists()


def test_sha_equality_flip_proceeds_over_the_drift(tmp_path):
    """RED pair: disable ONLY the sha equality test -> the drifted store is
    accepted; the refusal above is that clause alone."""
    store, snap = ready_grid(tmp_path)
    p = store / "AA.csv"
    p.write_text(p.read_text(encoding="utf-8")
                 + "2026-10-09,1,1,1,1,1\n", encoding="utf-8")
    mod = mutated_holdout(("if derived_sha != snap_sha:",
                           "if derived_sha != derived_sha:"))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                           ["MACD"])
    assert art["descriptive_only"]


def test_absent_frozen_doc_refuses_naming_the_path(tmp_path):
    store, snap = ready_grid(tmp_path)
    missing = tmp_path / "LEADERBOARD.md"               # never written
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, missing, 10, "val2")
    assert str(missing) in " ; ".join(exc.value.reasons)
    assert not holdout_root(snap).exists()
    assert manifest_of(snap)["holdout_opened"] is False  # nothing was spent


def test_doc_existence_flip_opens_without_the_freeze(tmp_path):
    """RED pair: disable ONLY the doc-existence check -> the holdout opens
    though no doc was ever frozen (a bare-touch doc is the fc1 file's)."""
    store, snap = ready_grid(tmp_path)
    mod = mutated_holdout(("if not doc_path.exists():", "if False:"))
    art = mod.open_holdout(snap, store, tmp_path / "NOPE.md", 10, "val2",
                           ["MACD"])
    assert art["descriptive_only"]


def test_manifest_without_fold_day_counts_refuses(tmp_path):
    store = make_store(tmp_path, ["AA"])
    snap = ready_snapshot(tmp_path, store, ["AA"])
    gc.run_pass(snap, "val2", store, LIGHT)
    man = manifest_of(snap)
    del man["cuts"]["fold_day_counts"]                   # the tampering
    write_manifest(snap, man)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")
    assert "fold_day_counts" in " ; ".join(exc.value.reasons)


def test_manifest_key_check_flip_crashes_instead(tmp_path):
    """RED pair: disable ONLY the key check -> the same tampered manifest is
    NOT refused cleanly; execution dies deeper (KeyError) — the clause above
    is what turns that crash into a named refusal."""
    store = make_store(tmp_path, ["AA"])
    snap = ready_snapshot(tmp_path, store, ["AA"])
    gc.run_pass(snap, "val2", store, LIGHT)
    man = manifest_of(snap)
    del man["cuts"]["fold_day_counts"]
    write_manifest(snap, man)
    mod = mutated_holdout(
        ("missing = [k for k in MANIFEST_CUT_KEYS if k not in cuts]",
         "missing = []"))
    with pytest.raises(KeyError):
        mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                         ["MACD"])


# ── CLI: exit codes + stdout vocabulary ─────────────────────────────────────

def test_cli_green_then_refusal_exit_codes(tmp_path):
    store, snap = ready_grid(tmp_path)
    doc = frozen_doc(tmp_path)
    ok = run_cli(snap, store, doc, "--combos", "MACD,RSI")
    assert ok.returncode == 0 and "holdout opened once" in ok.stdout, ok.stderr
    assert not scan_vocab(ok.stdout)                    # descriptive stdout
    bad = run_cli(snap, store, doc, "--top", "11", "--combos", "MACD")
    assert bad.returncode == 1 and "REFUSE" in bad.stderr
    assert "top-K = 11" in bad.stderr                   # K named on stderr too
