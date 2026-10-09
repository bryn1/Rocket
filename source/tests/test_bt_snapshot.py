"""bt_snapshot: frozen cuts, day-count floors, write-once, validator (T-BT2).

PLAN-c3 §4's build rule as a checked contract, T16-style: every guard ships
with its planted-RED proof next to the GREEN one — a check that has never
failed is not a check. Synthetic stores here are tiny (weekday dates × a few
CSVs); the geometry was computed once, so the day counts below are exact
pinning values (70/65/65/44 on 2025-11-03..2026-10-08 weekdays), not ranges.

The campaign pair that proves the FROZEN part of "frozen cuts" (dispatch
T-BT2): a second build on a store with a different data_last_bar freezes a
different C3, and snapshot-1's validator REJECTS snapshot-2's listing — a
snapshot is a statement about one store state, and never silently follows
the nightly growth (DA c1 P1-1). Zero network, no live store read.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
for _p in (str(TESTS), str(TESTS.parent / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_snapshot as bs                # noqa: E402
from bt_fixture_select import FIXTURE_DIR   # noqa: E402

# ── the geometry pinned by PLAN-c3 §4 (known data_last_bar -> known cuts) ───

GREEN_LAST = "2026-10-08"
EXPECTED_CUTS = {"C1": "2026-02-08", "C2": "2026-05-08", "C3": "2026-08-08"}
GREEN_COUNTS = {"train": 70, "val1": 65, "val2": 65, "holdout": 44}


def weekdays(a: str, b: str) -> list[str]:
    out, d = [], date.fromisoformat(a)
    while d <= date.fromisoformat(b):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_store(root: Path, dates: list[str], tickers=3) -> Path:
    """Synthetic store: identical weekday frame for N tickers (the counts the
    folds measure are panel-day counts, so a few fat files pin them exactly)."""
    store = root / "store"
    store.mkdir(parents=True, exist_ok=True)
    for i in range(tickers):
        body = "".join(f"{d},{i + 10.0},{i + 11.0},{i + 9.0},{i + 10.5},{(i + 1) * 100}\n"
                       for d in dates)
        (store / f"TICK{i}.csv").write_text(
            "date,open,high,low,close,volume\n" + body, encoding="utf-8")
    return store


# ── cuts: calendar arithmetic, month-end clamp (pure) ───────────────────────

def test_cuts_from_known_data_last_bar():
    assert bs.compute_cuts(GREEN_LAST) == EXPECTED_CUTS
    # month ends clamp into the shorter target month (calendar, not 30d):
    assert bs.compute_cuts("2026-05-31")["C1"] == "2025-09-30"
    assert bs.minus_months(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert bs.minus_months(date(2024, 5, 31), 3) == date(2024, 2, 29)  # leap
    assert bs.minus_months(date(2026, 3, 31), 12) == date(2025, 3, 31)


# ── build: freeze cuts + fold_day_counts + manifest shape (GREEN) ───────────

def test_build_freezes_cuts_counts_and_manifest_shape(tmp_path):
    store = make_store(tmp_path, weekdays("2025-11-03", GREEN_LAST))
    m = bs.build_snapshot(store, tmp_path / "snap", run_iso="2026-10-09T00:00:00Z")
    assert m["cuts"] == {**EXPECTED_CUTS, "fold_day_counts": GREEN_COUNTS}
    assert m["n_trials_path"] == 0                  # §4: init 0, never retro
    assert m["holdout_opened"] is False             # bt_holdout.py owns the flip
    assert m["chunk_state"] == {}
    assert m["store_n"] == 3 and m["data_last_bar"] == GREEN_LAST
    assert m["fixture_sha"] is None                 # synthetic store: no sums
    assert m["registry_ts"]                         # load_plan fact, imported
    assert set(m) == {"run_iso", "git_sha", "store_n", "store_listing_sha256",
                      "data_last_bar", "registry_ts", "fixture_sha", "params",
                      "chunk_state", "n_trials_path", "cuts", "holdout_opened"}
    on_disk = tmp_path / "snap" / ".tmp" / "backtest_manifest.json"
    assert json.loads(on_disk.read_text(encoding="utf-8")) == m
    assert bs.validate_snapshot(store, tmp_path / "snap") == []   # GREEN


# ── planted-red pair 1: floor — a truncated VAL fold FAILS, names its floor ─

def test_day_count_floors_fail_the_build_named(tmp_path):
    """Three synthetic short stores; each fails EXACTLY one §4 floor and the
    reason names it (a build that silently ships a weak fold is the failure
    this guards — PLAN-c3 §4: floors are build FAILURES, never warnings)."""
    cases = {
        "train": (weekdays("2026-01-05", GREEN_LAST),   # 25 stored days <= C1
                  "fold floor: train 25 < 60 days"),
        "val1": ([d for d in weekdays("2025-06-02", GREEN_LAST)
                  if not "2026-02-08" < d <= "2026-03-15"],   # gap the fold
                 "fold floor: val1 40 < 55 days"),
        "holdout": (weekdays("2025-06-02", "2026-08-10") + [GREEN_LAST],
                    "fold floor: holdout 2 < 30 days"),
    }
    for fold, (dates, msg) in cases.items():
        out = tmp_path / f"snap-{fold}"
        with pytest.raises(bs.SnapshotError) as exc:
            bs.build_snapshot(make_store(tmp_path / fold, dates), out,
                              run_iso="2026-10-09T00:00:00Z")
        assert exc.value.reasons == [msg], fold
        assert not bs.manifest_path(out).exists(), "fail-closed: nothing written"


def test_empty_store_fails_named(tmp_path):
    empty = tmp_path / "nostore"
    empty.mkdir()
    with pytest.raises(bs.SnapshotError) as exc:
        bs.build_snapshot(empty, tmp_path / "snap")
    assert "no CSVs" in exc.value.reasons[0]


# ── planted-red pairs 2+3: validator rejects tampered sha / drifted cuts ────

def _green(tmp_path) -> tuple[Path, Path]:
    store = make_store(tmp_path, weekdays("2025-11-03", GREEN_LAST))
    bs.build_snapshot(store, tmp_path / "snap", run_iso="2026-10-09T00:00:00Z")
    return store, tmp_path / "snap"


def _edit_manifest(out: Path, mutate) -> None:
    path = bs.manifest_path(out)
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_validator_rejects_a_tampered_listing_sha(tmp_path):
    store, out = _green(tmp_path)
    assert bs.validate_snapshot(store, out) == []             # GREEN first
    def flip(doc):                                            # one hex char
        sha = doc["store_listing_sha256"]
        doc["store_listing_sha256"] = ("0" if sha[0] != "0" else "1") + sha[1:]
    _edit_manifest(out, flip)
    violations = bs.validate_snapshot(store, out)
    assert len(violations) == 1 and "listing sha" in violations[0]   # RED


def test_validator_rejects_drifted_frozen_cuts(tmp_path):
    store, out = _green(tmp_path)
    _edit_manifest(out, lambda d: d["cuts"].__setitem__(
        "C3", bs.minus_months(date.fromisoformat(d["cuts"]["C3"]), 1).isoformat()))
    violations = bs.validate_snapshot(store, out)
    assert len(violations) == 1 and "frozen cuts" in violations[0]


def test_validator_rejects_store_drift_the_sha_can_see(tmp_path):
    """The store-side of the listing lock: PLAN-c3 §4 states this sha pins
    LISTING (name + last_bar), NOT bar content — a mutated price keeps the
    same last_bar and stays invisible BY DESIGN (fixture byte-lock tests own
    byte drift). What nightly growth/dropout does change is last_bar and the
    file set: one file truncated at the tail, and one file deleted, must both
    go RED here."""
    store, out = _green(tmp_path)
    lines = (store / "TICK1.csv").read_text(encoding="utf-8").splitlines()
    (store / "TICK1.csv").write_text("\n".join(lines[:-1]) + "\n",
                                     encoding="utf-8")
    violations = bs.validate_snapshot(store, out)
    assert len(violations) == 1 and "listing sha" in violations[0]
    (store / "TICK2.csv").unlink()
    assert bs.validate_snapshot(store, out) != []


# ── write-once + THE PAIR: second data_last_bar -> other C3, cross-reject ───

def test_write_once_second_build_refuses(tmp_path):
    store = make_store(tmp_path, weekdays("2025-11-03", GREEN_LAST))
    out = tmp_path / "snap"
    bs.build_snapshot(store, out, run_iso="2026-10-09T00:00:00Z")
    with pytest.raises(bs.SnapshotError) as exc:
        bs.build_snapshot(store, out, run_iso="2026-10-10T00:00:00Z")
    assert "write-once" in exc.value.reasons[0]


def test_pair_second_data_last_bar_freezes_other_c3_cross_rejects(tmp_path):
    """Dispatch T-BT2 pair (planted-red half of the campaign's drift guard):
    snapshot-2 built from a store ending 2026-09-25 freezes a DIFFERENT C3,
    snapshot-1's validator REJECTS snapshot-2's listing (same store, later/
    earlier state -> different sha), and each snapshot still validates green
    against its OWN store."""
    dates_1 = weekdays("2025-11-03", GREEN_LAST)
    dates_2 = weekdays("2025-11-03", "2026-09-25")
    store_1, store_2 = make_store(tmp_path / "s1", dates_1), \
        make_store(tmp_path / "s2", dates_2)
    m1 = bs.build_snapshot(store_1, tmp_path / "snap1",
                           run_iso="2026-10-09T00:00:00Z")
    m2 = bs.build_snapshot(store_2, tmp_path / "snap2",
                           run_iso="2026-10-09T00:00:01Z")
    assert m1["cuts"]["C3"] == "2026-08-08"
    assert m2["cuts"]["C3"] == "2026-07-25"           # different last bar...
    assert m2["cuts"]["C3"] != m1["cuts"]["C3"]        # ... -> frozen C3 moves
    violations = bs.validate_snapshot(store_2, tmp_path / "snap1")
    assert violations and "listing sha" in violations[0]           # RED
    assert bs.validate_snapshot(store_1, tmp_path / "snap1") == []  # GREEN
    assert bs.validate_snapshot(store_2, tmp_path / "snap2") == []  # GREEN


# ── the fixture itself snapshots: the campaign's hermetic E2E path ──────────

def test_fixture300_builds_and_validates(tmp_path):
    import hashlib
    m = bs.build_snapshot(FIXTURE_DIR, tmp_path / "snap300",
                          run_iso="2026-10-09T00:00:00Z")
    assert m["store_n"] == 300 and m["data_last_bar"] == GREEN_LAST
    assert m["fixture_sha"] == hashlib.sha256(
        (FIXTURE_DIR / "SHA256SUMS").read_bytes()).hexdigest()
    assert m["cuts"] == {**EXPECTED_CUTS,
                         "fold_day_counts": {"train": 89, "val1": 65,
                                             "val2": 65, "holdout": 44}}
    assert bs.validate_snapshot(FIXTURE_DIR, tmp_path / "snap300") == []
