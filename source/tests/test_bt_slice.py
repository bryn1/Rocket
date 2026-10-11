"""bt_slice fold materialization (T-BT4, FIX cycle 1): the §4 windows cut on
the manifest's FROZEN cuts — boundary EXACT (train = everything ≤ C1 = THE
SELECTION ROOT; C2 in trainval, C2+1 absent), no slice ever touches the
holdout, excluded (bar-gate) tickers never materialize, and re-slicing is
byte-identical. Refusals are always named."""
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

import bt_bargate as bg                             # noqa: E402
import bt_slice                                     # noqa: E402
import bt_snapshot as bs                            # noqa: E402
import bt_streams as st                             # noqa: E402
from bt_fold_fixtures import (FAT_DATES, LIGHT, RUN_ISO, gz_lines,  # noqa: E402
                              make_store, ready_snapshot)


def slice_dates(snap: Path, fold: str, ticker: str = "AA") -> list[str]:
    return [b["d"] for b in gz_lines(snap / "slices" / fold / ticker
                                     / "RSI.jsonl.gz")[1:]]


def test_fold_keeps_windows_are_the_frozen_cut_geometry():
    cuts = {"C1": "2026-02-08", "C2": "2026-05-08", "C3": "2026-08-08"}
    assert bt_slice.fold_keeps("train", "2026-02-08", cuts)      # C1 IN
    assert not bt_slice.fold_keeps("train", "2026-02-09", cuts)  # C1+1 OUT
    assert bt_slice.fold_keeps("trainval", "2026-02-08", cuts)      # C1 IN
    assert bt_slice.fold_keeps("trainval", "2026-05-08", cuts)      # C2 IN
    assert not bt_slice.fold_keeps("trainval", "2026-05-09", cuts)  # C2+1 OUT
    assert not bt_slice.fold_keeps("val1", "2026-02-08", cuts)      # C1 OUT
    assert bt_slice.fold_keeps("val1", "2026-02-09", cuts)
    assert not bt_slice.fold_keeps("val2", "2026-05-08", cuts)      # C2 OUT
    assert bt_slice.fold_keeps("val2", "2026-08-08", cuts)          # C3 IN
    assert not bt_slice.fold_keeps("val2", "2026-08-09", cuts)      # HOLDOUT
    with pytest.raises(bt_slice.SliceError):
        bt_slice.fold_keeps("holdout", "2026-09-01", cuts)


def test_train_selection_root_boundary_is_c1_exact(tmp_path):
    """THE SELECTION ROOT (DA-P0-1): train = everything <= C1. The C1 date is
    IN, C1+1 (= first VAL-1 bar) is ABSENT — selection can never see VAL-1."""
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    s = bt_slice.materialize_slices(snap)
    c1 = json.loads(bs.manifest_path(snap).read_text(encoding="utf-8")
                    )["cuts"]["C1"]
    ds = slice_dates(snap, "train")
    # C1 may fall on a non-trading day — the root's max is the LAST STORED
    # TRADING DAY <= C1 (the store holds weekdays only):
    assert max(ds) == max(d for d in FAT_DATES if d <= c1)
    assert not [d for d in ds if d > c1]        # first bar past C1: ABSENT
    assert s["train"]["last_bar"] == max(d for d in FAT_DATES if d <= c1)
    exp = sum(d <= c1 for d in FAT_DATES[bg.WARMUP_BARS:])
    assert s["train"]["files"] == 4 and s["train"]["bars"] == 4 * exp


def test_trainval_boundary_excludes_the_first_bar_past_c2(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    s = bt_slice.materialize_slices(snap)
    c2 = json.loads(bs.manifest_path(snap).read_text(encoding="utf-8")
                    )["cuts"]["C2"]
    ds = slice_dates(snap, "trainval")
    assert max(ds) == c2                        # the C2 date is INCLUDED
    assert not [d for d in ds if d > c2]        # C2+1 and past: ABSENT
    assert s["trainval"]["last_bar"] == c2
    exp = sum(d <= c2 for d in FAT_DATES[bg.WARMUP_BARS:])
    assert s["trainval"]["files"] == 4 and s["trainval"]["bars"] == 4 * exp


def test_val_folds_cover_their_windows_exactly(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    bt_slice.materialize_slices(snap)
    cuts = json.loads(bs.manifest_path(snap).read_text(
        encoding="utf-8"))["cuts"]
    d1, d2 = slice_dates(snap, "val1"), slice_dates(snap, "val2")
    assert min(d1) > cuts["C1"] and max(d1) == cuts["C2"]
    assert min(d2) > cuts["C2"] and max(d2) <= cuts["C3"]
    assert not any(d > cuts["C3"] for fold in bt_slice.FOLDS
                   for d in slice_dates(snap, fold))   # no holdout, EVER
    hdr = gz_lines(snap / "slices/val2/AA/RSI.jsonl.gz")[0]   # own range:
    assert hdr["slice"] == "val2" and hdr["slice_first_bar"] == min(d2)
    assert hdr["slice_last_bar"] == max(d2) and hdr["slice_bars"] == len(d2)
    assert hdr["cuts"]["C2"] == cuts["C2"]      # the FROZEN cut it was cut by


def test_excluded_ticker_never_materializes(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    (store / "THIN.csv").write_text("date,open,high,low,close,volume\n"
                                    "2026-10-08,1,1,1,1,1\n", encoding="utf-8")
    bs.build_snapshot(store, tmp_path / "snap", run_iso=RUN_ISO)
    st.build_streams(tmp_path / "snap", store, indicators=LIGHT, workers=1)
    bt_slice.materialize_slices(tmp_path / "snap")
    for fold in bt_slice.FOLDS:
        assert not (tmp_path / "snap" / "slices" / fold / "THIN").exists()
    assert not (tmp_path / "snap" / "streams" / "THIN").exists()


def test_two_slice_runs_byte_identical(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    first = {str(p.relative_to(snap)): p.read_bytes()
             for p in sorted((snap / "slices").rglob("*")) if p.is_file()}
    bt_slice.materialize_slices(snap)
    again = {str(p.relative_to(snap)): p.read_bytes()
             for p in sorted((snap / "slices").rglob("*")) if p.is_file()}
    assert again == first and len(first) == 17    # 16 slice (4 folds) + manifest


def test_slice_refusals_are_named(tmp_path):
    with pytest.raises(bt_slice.SliceError) as exc:
        bt_slice.materialize_slices(tmp_path / "none")
    assert "no campaign snapshot manifest" in str(exc.value)
    store = make_store(tmp_path, ["AA"])
    bs.build_snapshot(store, tmp_path / "snap", run_iso=RUN_ISO)
    with pytest.raises(bt_slice.SliceError) as exc:
        bt_slice.materialize_slices(tmp_path / "snap")
    assert "no streams root" in str(exc.value)


def test_cli_boundary_and_refusal_exit_codes(tmp_path):
    store = make_store(tmp_path, ["AA", "BB"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB"])
    ok = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_slice.py"),
         "--snapshot-dir", str(snap), "--folds", "trainval"],
        capture_output=True, text=True, timeout=300)
    assert ok.returncode == 0 and "slices/trainval" in ok.stdout, ok.stderr
    bad = subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_slice.py"),
         "--snapshot-dir", str(tmp_path / "nosnap")],
        capture_output=True, text=True, timeout=300)
    assert bad.returncode == 1 and "REFUSE" in bad.stderr
