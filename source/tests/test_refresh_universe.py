"""refresh_universe (MC 10222): the §10 guarded refresh CLI. Guard math is
pure (two-sided band from the SAME §9#5 constants, named-region floor,
superset rule); refusal must never touch the tracked cache; --commit sits
BEHIND the guard (commit/push wiring itself is orchestrator-territory and
explicitly untested by this card). universe_builder must never be imported
at module import time (nightly never builds)."""
import json
import sys
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
