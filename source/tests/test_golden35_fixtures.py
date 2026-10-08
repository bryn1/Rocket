"""The golden-35 continuity pin and the FROZEN store it runs on (MC 10304).

§3 test (b) of MC 10223 — the 35 demo tickers must score EXACTLY as published
at f2c02f6 — moved here from test_demo_page_generator.py, because the pin is
only a claim about that data state while the bytes behind it are pinned too:

* ``fx.GOLDEN35_DIR`` (tests/data/golden35) is the 35 CSVs as the nightly
  store held them on 2026-10-07, committed as test DATA. The pin scores
  THESE bytes and never the live store at source/data/raw: the nightly
  rewrites that dir daily and scores legitimately move with it (VERIFIED
  2026-10-08: the refetched store scored MSFT 59.8 against the pinned 57.8).
* ``SHA256SUMS`` (one manifest, 35 hashes) byte-locks the store, and the
  mutation tests below prove that lock goes RED — so "f2c02f6 data state"
  is a checked claim, not a hope.

No network, no live store: nothing in this module needs source/data/raw to
exist, and it skips on nothing.
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
import demo_page_fixtures as fx         # noqa: E402,F401 (pin asserts import)
from demo_page_fixtures import GOLDEN35_DIR, GOLDEN_35    # noqa: E402

MANIFEST = "SHA256SUMS"                  # sha256sum -c format, DATA not logic


# ── the pin: §3 test (b), scored off the frozen fixture store ───────────────

def test_golden35_scores_unchanged_by_region_map():
    """The 35 demo tickers score EXACTLY as published at f2c02f6 through the
    same app seam (usa/sweden/germany mappings kept), off fx.GOLDEN35_DIR.
    NO skip path: the fixture is committed, so a missing CSV is a RED."""
    import app
    from rocket.backtest.indicator_eval import to_indicator_frame
    region_of = lambda t: ("sweden" if t.endswith(".ST")
                           else "germany" if t.endswith(".DE") else "usa")
    for ticker, expected in GOLDEN_35.items():
        path = GOLDEN35_DIR / full_universe.cache_filename(ticker)
        assert path.exists(), f"frozen golden-35 fixture missing: {path}"
        df = store_io.read_cache(path)
        idf = to_indicator_frame(df)
        summary, _ = app._compute_all_indicators(idf)
        rs = app._score_from_summary(summary, ticker=ticker,
                                     region=region_of(ticker))["rocket_score"]
        assert f"{rs.overall_score:.1f}" == expected, ticker


# ── byte-lock manifest: the store is the f2c02f6 bytes, or the suite is RED ─

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
    the only GREEN answer): a mutated bar, a missing pinned CSV, an extra
    un-manifested CSV, or a manifest that stops covering the 35 golden CSVs."""
    rows = manifest_rows(store)
    pinned = {full_universe.cache_filename(t) for t in GOLDEN_35}
    out = [f"missing pinned CSV {n}" for n in sorted(rows)
           if not (store / n).exists()]
    out += [f"HASH MISMATCH {n}" for n in sorted(rows)
            if (store / n).exists() and sha256_of(store / n) != rows[n]]
    out += [f"golden ticker absent from the manifest {n}"
            for n in sorted(pinned - set(rows))]
    out += [f"manifest row for a non-golden CSV {n}"
            for n in sorted(set(rows) - pinned)]
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
    assert GOLDEN35_DIR.is_dir() and GOLDEN35_DIR.parent == TESTS / "data"
    assert GOLDEN35_DIR != full_universe.CACHE_DIR


def test_frozen_store_manifest_locks_every_pinned_csv():
    assert len(GOLDEN_35) == 35
    assert len(manifest_rows(GOLDEN35_DIR)) == 35
    assert byte_lock_violations(GOLDEN35_DIR) == []


def test_byte_lock_goes_red_on_one_mutated_bar(tmp_path):
    """Red-proof of the gate above: the SAME check against a copy holding one
    mutated bar must name that file — and the pin must go red with it."""
    store = tmp_path / "golden35"
    shutil.copytree(GOLDEN35_DIR, store)
    mutate_one_bar(store / "MSFT.csv")
    assert byte_lock_violations(store) == ["HASH MISMATCH MSFT.csv"]


def test_byte_lock_goes_red_on_a_dropped_or_extra_csv(tmp_path):
    """The two other drift classes: a dropped pinned CSV and an extra
    un-manifested CSV (a shrunk or padded store is still drift)."""
    store = tmp_path / "golden35"
    shutil.copytree(GOLDEN35_DIR, store)
    (store / "DIS.csv").unlink()
    assert byte_lock_violations(store) == ["missing pinned CSV DIS.csv"]
    shutil.rmtree(store)
    shutil.copytree(GOLDEN35_DIR, store)
    (store / "EXTRA.csv").write_text("date,open,high,low,close,volume\n",
                                     encoding="utf-8")
    assert byte_lock_violations(store) == [
        "un-manifested CSV in the frozen store EXTRA.csv"]


def test_golden35_pin_ignores_a_live_store_that_drifted(monkeypatch, tmp_path):
    """Hermeticity guard (the MC 10304 bug itself): a live store that IS
    present and HAS drifted (one bar mutated) must not move the pin. Pre-fix
    the pin read that dir and went RED on 2026-10-08 — MSFT 59.8 vs 57.8 —
    purely because the nightly refetched. This is the daily time-bomb, now
    impossible: the pin reads fx.GOLDEN35_DIR only."""
    live = tmp_path / "raw"
    shutil.copytree(GOLDEN35_DIR, live)
    mutate_one_bar(live / "MSFT.csv")
    monkeypatch.setattr(full_universe, "CACHE_DIR", live)
    monkeypatch.setattr(store_io, "CACHE_DIR", live)
    test_golden35_scores_unchanged_by_region_map()      # stays GREEN
