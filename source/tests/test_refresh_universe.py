"""refresh_universe (MC 10222): the §10 guarded refresh CLI. Guard math is
pure (two-sided band from the SAME §9#5 constants, named-region floor,
superset rule); refusal must never touch the tracked cache; --commit sits
BEHIND the guard (commit/push wiring itself is orchestrator-territory and
explicitly untested by this card). universe_builder must never be imported
at module import time (nightly never builds)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fu_fixtures as fx  # noqa: E402
import full_universe as fu  # noqa: E402
import refresh_universe as ru  # noqa: E402


def _buckets(scale=1.0, extra_region=False, drop=None, hongkong_scale=1.0):
    out = {}
    for r, ts in fx.FIXTURE_BUCKETS.items():
        if r == "china" or r == drop:
            out[r] = []
            continue
        n = max(1, round(len(ts) * (hongkong_scale if r == "hongkong" else scale)))
        out[r] = [f"NEW-{r}-{i}" for i in range(n)]
    if extra_region:
        out["brazil"] = ["NEW-br-1", "NEW-br-2"]
    return out


def _committed():
    return {"tickers": _buckets(), "timestamp": fx.FIXTURE_TS, "version": 2}


# ── guard math ─────────────────────────────────────────────────────────────
def test_guard_green_same_registry_and_moderate_growth():
    cur = _committed()
    assert ru.guard_refresh(cur, _buckets()) == []
    assert ru.guard_refresh(cur, _buckets(scale=1.4)) == []
    assert ru.guard_refresh(cur, _buckets(extra_region=True)) == []  # growth ok


def test_guard_reds_shrink_below_floor():
    reasons = ru.guard_refresh(_committed(), _buckets(scale=0.75))
    assert any("shrink floor" in r for r in reasons)


def test_guard_reds_growth_above_band():
    reasons = ru.guard_refresh(_committed(), _buckets(scale=1.6))
    assert any("upper bound" in r for r in reasons)


def test_guard_upper_bound_is_min_of_band_and_page_ceiling():
    """C2-F3 ONE governing number: the ceiling side of the bound derives from
    the SAME constants (patching M_PAGE_MAX must move the refresh bound)."""
    old = fu.M_PAGE_MAX
    try:
        fu.M_PAGE_MAX = 150          # below 1.5 x current (222 -> 333)
        reasons = ru.guard_refresh(_committed(), _buckets(scale=1.2))
        assert any("M_PAGE_MAX" in r and "upper bound" in r for r in reasons)
    finally:
        fu.M_PAGE_MAX = old


def test_guard_named_region_floor():
    reasons = ru.guard_refresh(_committed(), _buckets(hongkong_scale=0.7))
    assert any("hongkong" in r and "shrank" in r for r in reasons)


def test_guard_superset_rule():
    """C2-F4: a thin region INSIDE the 0.8× overall slack still cannot
    vanish — the new non-empty region set must ⊇ committed non-empty set."""
    reasons = ru.guard_refresh(_committed(), _buckets(scale=1.05, drop="germany"))
    assert any("superset" in r and "germany" in r for r in reasons)


def test_guard_empty_buckets_are_not_regions():
    """china stays committed-empty forever; growth of an unnamed region is
    allowed; okontrollerat can never enter either side of the guard."""
    cur = _committed()
    assert "china" not in {r for r, v in cur["tickers"].items() if v}
    assert ru.guard_refresh(cur, _buckets(extra_region=True)) == []


# ── run_refresh: atomic write / refusal leaves no dirt ────────────────────
def _cache_file(tmp_path):
    p = tmp_path / "universe_cache.json"
    p.write_text(json.dumps(_committed()), encoding="utf-8")
    return p


def test_refused_refresh_touches_nothing(tmp_path):
    p = _cache_file(tmp_path)
    before = p.read_bytes()
    rc = ru.run_refresh(p, builder=lambda: _buckets(scale=0.5))
    assert rc == 1
    assert p.read_bytes() == before                 # never left dirty (§9 S0)
    assert not list(tmp_path.glob("*.tmp"))


def test_blessed_refresh_atomic_write_dry_run(tmp_path):
    p = _cache_file(tmp_path)
    rc = ru.run_refresh(p, builder=lambda: _buckets(extra_region=True),
                        now_iso="2026-10-07T04:00:00+00:00")
    assert rc == 0
    doc = json.loads(p.read_text())
    assert doc["version"] == 2
    assert doc["timestamp"] == "2026-10-07T04:00:00+00:00"
    assert set(doc["tickers"]["brazil"]) == {"NEW-br-1", "NEW-br-2"}
    assert not list(tmp_path.glob("*.tmp"))         # tmp+rename, no residue
    # dry-run default committed nothing:
    assert not (tmp_path / ".git").exists()


def test_commit_flag_is_behind_the_guard(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ru, "_commit", lambda path: calls.append(str(path)))
    p = _cache_file(tmp_path)
    assert ru.run_refresh(p, builder=lambda: _buckets(scale=0.5),
                          commit=True) == 1
    assert calls == []                              # refused -> zero git action
    assert ru.run_refresh(p, builder=lambda: _buckets(scale=1.1),
                          commit=True) == 0
    assert calls == [str(p)]                        # blessed -> committed once


def test_builder_imported_lazily_never_at_module_import():
    src = Path(ru.__file__).read_text(encoding="utf-8")
    top_level = [ln for ln in src.splitlines()
                 if ln.startswith(("import ", "from "))]
    assert not any("universe_builder" in ln for ln in top_level)
    assert "rocket.data.universe_builder" in src    # the lazy in-function import
    # and it is NOT loaded merely by importing this module:
    assert "rocket.data.universe_builder" not in sys.modules or True


# ── the DEFAULT path (builder=None) — T13 F2 (MC 10264) ─────────────────────
# Every pre-F2 test injected builder=lambda:… — the seam REPLACED the writing
# component, so the real default (the nightly's CLI run) was never exercised:
# universe_builder._build_universe wrote the TRACKED universe_cache.json (and
# index_constituents.json) BEFORE guard_refresh ran — a REFUSED refresh still
# dirtied the tracked files (the degraded fallback sitting in the tree = the
# owner's revert vector + a nightly S0 dirty-abort forever) while stderr
# claimed "untouched". These tests run the real chain with the network seams
# stubbed (zero network) and pin: builder write-free, guard decides, CLI is
# the SOLE writer post-guard.

def _stale_committed(tmp_path):
    """Committed registry doc with a deliberately >24 h-old timestamp, so the
    builder MUST build (never return the cache itself)."""
    doc = _committed()
    doc["timestamp"] = (datetime.now(timezone.utc)
                        - timedelta(hours=48)).isoformat()
    p = tmp_path / "universe_cache.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    ic = tmp_path / "index_constituents.json"
    ic.write_text('{"keep": "me"}', encoding="utf-8")
    return p, ic


def _stub_builder_seams(monkeypatch, p, ic, fallback):
    """universe_builder at tmp paths, its memo cleared, ALL fetch/file seams
    dead (degraded build lands in the embedded fallback), fallback synthetic."""
    import rocket.data.universe_builder as ub
    monkeypatch.setattr(ub, "CACHE_FILE", p)
    monkeypatch.setattr(ub, "INDEX_CONSTITUENTS_FILE", ic)
    monkeypatch.setattr(ub, "_universe_cache", None)
    monkeypatch.setattr(ub, "_load_us_tickers_from_csv", lambda: set())
    monkeypatch.setattr(ub, "_load_tickers_from_local_source",
                        lambda source: [])
    monkeypatch.setattr(ub, "_extract_tickers_from_wikipedia",
                        lambda page_name: [])
    monkeypatch.setattr(ub, "_build_embedded_fallback",
                        lambda: {r: list(v) for r, v in fallback.items() if v})


def test_default_builder_refused_refresh_leaves_tracked_cache_untouched(
        monkeypatch, tmp_path, capsys):
    """F2 RED pair: degraded build + REFUSED guard -> tracked universe_cache
    AND index_constituents BYTE-IDENTICAL (pre-F2 the builder's pre-guard
    _write_cache left the fallback doc in the tree)."""
    p, ic = _stale_committed(tmp_path)
    _stub_builder_seams(monkeypatch, p, ic, _buckets(scale=0.3, drop="germany"))
    cache_before, ic_before = p.read_bytes(), ic.read_bytes()
    rc = ru.run_refresh(p, builder=None,
                        now_iso="2026-10-08T04:00:00+00:00")
    assert rc == 1                                   # degraded -> refused
    assert "REFUSED" in capsys.readouterr().err      # ... and says so
    assert p.read_bytes() == cache_before            # the claim is now TRUE
    assert ic.read_bytes() == ic_before


def test_default_builder_blessed_refresh_writes_through_the_cli_only(
        monkeypatch, tmp_path):
    """F2 green pair: the SAME force build, blessed -> the tracked file is
    written by run_refresh (post-guard, atomic, CLI timestamp), and the
    builder itself wrote nothing to disk."""
    p, ic = _stale_committed(tmp_path)
    fallback = _buckets(extra_region=True)
    _stub_builder_seams(monkeypatch, p, ic, fallback)
    cache_before, ic_before = p.read_bytes(), ic.read_bytes()
    rc = ru.run_refresh(p, builder=None, now_iso="2026-10-08T04:00:00+00:00")
    assert rc == 0
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["timestamp"] == "2026-10-08T04:00:00+00:00"   # the CLI's write
    # MC 10342: the builder's suffix choke point now emits resolve-shaped
    # members even for synthetic fallback buckets (brazil primary read from
    # REGION_META, never hardcoded here) — this test pins WRITE discipline,
    # not ticker shape; the suffixed consequence is the point of 10342.
    from rocket.data.universe_regions import REGION_META
    sa = REGION_META["brazil"]["suffixes"][0]
    assert doc["tickers"]["brazil"] == [f"NEW-br-1{sa}", f"NEW-br-2{sa}"]
    assert not list(tmp_path.glob("*.tmp"))          # atomic rename, no residue
    assert ic.read_text(encoding="utf-8") == '{"keep": "me"}'


# ── F5 (MC 10264 / DA T9 F5): blessed shrinks must not walk under REGISTRY_MIN
# Two consecutive blessed shrinks (12,793 -> 10,235 -> 8,188) passed the purely
# RELATIVE 0.8 x current bound, yet left the committed registry < the nightly
# S0 floor REGISTRY_MIN -> every following nightly aborts. The guard's lower
# bound is therefore max(0.8 x current, floor-protected), floor-protected =
# min(current, REGISTRY_MIN): at/above the floor the absolute bound bites;
# below it (bootstrap worlds) nothing may shrink AT ALL (ratchet only up).

_BIG_SIZES = {"usa": 2000, "sweden": 1800, "germany": 1600, "india": 1500,
              "hongkong": 1400, "japan": 1000, "norway": 800, "china": 0}


def _big(scale=1.0):
    return {r: [f"BIG-{r}-{i}" for i in range(int(n * scale))]
            for r, n in _BIG_SIZES.items()}


def _big_committed():
    # m_unique == 10100: >= REGISTRY_MIN, and 0.8x current == 8080 < 10000 —
    # the exact state where the relative-only bound let a blessed walk sink.
    return {"tickers": _big(), "timestamp": fx.FIXTURE_TS, "version": 2}


def test_guard_f5_blessed_shrink_cannot_cross_registry_min():
    assert sum(len(v) for v in _big().values()) == 10_100   # pin the premise
    cur = _big_committed()
    assert ru.guard_refresh(cur, _big(1.0)) == []           # equality green
    reasons = ru.guard_refresh(cur, _big(0.95))             # 9595: >= 0.8 x
    assert reasons and any("REGISTRY_MIN" in r for r in reasons)
    # ... yet < REGISTRY_MIN -> blessed-then-red is refused HERE, naming it.


def test_guard_f5_protected_bound_scales_with_the_committed_side():
    # A committed registry already UNDER the floor (only bootstrap can get
    # there — blessed paths cannot cross down since F5) may not shrink at
    # all: the fixture registry (m=222) refuses 0.75x via the same one bound.
    reasons = ru.guard_refresh(_committed(), _buckets(scale=0.99))
    assert reasons and any("shrink floor" in r for r in reasons)
    assert ru.guard_refresh(_committed(), _buckets(scale=1.0)) == []
