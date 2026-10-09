"""The FROZEN bt_fixture300 campaign store (MC 10386 T-BT2, PLAN-c3 §6).

The hermetic input for the whole indicator-backtest campaign (T-BT3..T-BT7):
300 CSVs copied byte-for-byte out of the nightly store on 2026-10-09 and
committed as test DATA. The discipline is the golden35 idiom verbatim
(test_golden35_fixtures.py): the store is byte-locked by ONE SHA256SUMS
manifest, and the mutation tests below prove that lock goes RED — so "frozen
fixture" is a checked claim, not a hope. Tests here never read
source/data/raw: the nightly rewrite moves that dir daily (MC 10304).

What the fixture must keep true for the campaign (PLAN-c3):
* deterministic stratified selection — function of ticker name + seed
  20261009 (bt_fixture_select.py) reproduces EXACTLY these 300 names from the
  committed build listing (bt_fixture300_candidates.csv);
* the 15 registry regions present in the store are all represented;
* a bar-count spread INCLUDING thin files (min 1 bar < WARMUP_BARS 60) so the
  §3 bar gate ("Stage-B drops tickers with < WARMUP+max-horizon bars") is
  testable on the fixture itself.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
for _p in (str(TESTS), str(TESTS.parent / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_universe                    # noqa: E402
import store_io                         # noqa: E402
from bt_fixture_select import (         # noqa: E402
    FIXTURE_DIR, MID_BARS, THIN_BARS, load_candidates, select_fixture300)

MANIFEST = "SHA256SUMS"                  # sha256sum -c format, DATA not logic


# ── byte-lock manifest: the fixture is the 2026-10-09 bytes, or RED ─────────

def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_rows(store: Path) -> dict[str, str]:
    """``<hex>  <name>`` rows, the exact shape ``sha256sum`` writes."""
    rows = {}
    for line in (store / MANIFEST).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split("  ", 1)
            rows[name.strip()] = digest.strip()
    return rows


def byte_lock_violations(store: Path) -> list[str]:
    """Every way the frozen store may drift from its manifest (sorted; [] is
    the only GREEN answer): a mutated bar, a missing pinned CSV, or an extra
    un-manifested CSV (the golden35 drift classes, fixture-size pinned at 300)."""
    rows = manifest_rows(store)
    out = [f"missing pinned CSV {n}" for n in sorted(rows)
           if not (store / n).exists()]
    out += [f"HASH MISMATCH {n}" for n in sorted(rows)
            if (store / n).exists() and sha256_of(store / n) != rows[n]]
    out += [f"un-manifested CSV in the frozen store {n}" for n in sorted(
        {p.name for p in store.glob("*.csv")} - set(rows))]
    return out


def mutate_one_bar(csv_path: Path) -> None:
    """Planted-bad surgery: one bar of one frozen ticker, close + 1.0."""
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    row = lines[-1].split(",")
    row[4] = f"{float(row[4]) + 1.0:.10f}"
    lines[-1] = ",".join(row)
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_frozen_store_is_a_fixture_not_the_live_store():
    assert FIXTURE_DIR.is_dir() and FIXTURE_DIR.parent == TESTS / "data"
    assert FIXTURE_DIR != full_universe.CACHE_DIR


def test_frozen_store_manifest_locks_every_pinned_csv():
    assert len(manifest_rows(FIXTURE_DIR)) == 300
    assert byte_lock_violations(FIXTURE_DIR) == []


def test_byte_lock_goes_red_on_one_mutated_bar(tmp_path):
    """Red-proof of the gate above (planted red): the SAME check against a
    copy holding one mutated bar must name that file."""
    store = tmp_path / "bt_fixture300"
    shutil.copytree(FIXTURE_DIR, store)
    victim = sorted(manifest_rows(store))[0]
    mutate_one_bar(store / victim)
    assert byte_lock_violations(store) == [f"HASH MISMATCH {victim}"]


def test_byte_lock_goes_red_on_a_dropped_or_extra_csv(tmp_path):
    """The two other drift classes: a dropped pinned CSV and an extra
    un-manifested CSV (a shrunk or padded fixture is still drift)."""
    store = tmp_path / "bt_fixture300"
    shutil.copytree(FIXTURE_DIR, store)
    victim = sorted(manifest_rows(store))[0]
    (store / victim).unlink()
    assert byte_lock_violations(store) == [f"missing pinned CSV {victim}"]
    shutil.rmtree(store)
    shutil.copytree(FIXTURE_DIR, store)
    (store / "EXTRA.csv").write_text("date,open,high,low,close,volume\n",
                                     encoding="utf-8")
    assert byte_lock_violations(store) == [
        "un-manifested CSV in the frozen store EXTRA.csv"]


# ── determinism: the committed bytes ARE the seeded stratified selection ────

def test_selection_reproduces_the_frozen_300_from_committed_inputs():
    """select_fixture300 over the committed build listing + seed 20261009
    returns EXACTLY the manifest's names; input order cannot move it (the
    per-name rank has no RNG stream)."""
    cands = load_candidates()
    sel = select_fixture300(cands)
    assert sel == sorted(manifest_rows(FIXTURE_DIR))
    assert select_fixture300(list(reversed(cands))) == sel
    assert select_fixture300(cands) == sel           # pure, twice, same output


def test_selection_covered_all_15_regions_and_the_bar_spread():
    """PLAN-c3 §6 T-BT2: stratified over the 15 regions present, spread incl.
    thin files (the §3 bar gate needs bars < WARMUP_BARS inside the fixture).
    Regions/bar counts come from the committed build listing, not the store."""
    by_name = {n: (r, b) for n, r, b in load_candidates()}
    sel = sorted(manifest_rows(FIXTURE_DIR))
    regions = {by_name[n][0] for n in sel}
    bars = [by_name[n][1] for n in sel]
    assert len(regions) == 15
    assert min(bars) < THIN_BARS and max(bars) >= MID_BARS
    thin = sum(b < THIN_BARS for b in bars)
    mid = sum(THIN_BARS <= b < MID_BARS for b in bars)
    tall = sum(b >= MID_BARS for b in bars)
    assert thin >= 10 and mid >= 10 and tall >= 100, (thin, mid, tall)


# ── the fixture loads through the real reader (it IS store-shaped data) ─────

def test_every_fixture_csv_reads_through_store_io():
    for path in sorted(FIXTURE_DIR.glob("*.csv")):
        df = store_io.read_cache(path)
        assert store_io.last_bar_date(df) != "", path.name
