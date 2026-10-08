#!/usr/bin/env python3
"""Guarded registry refresh CLI (DESIGN §10, MC 10222).

The nightly pipeline NEVER builds the registry — universe_builder's force
refresh silently fills thin regions from its ~1,200-ticker embedded fallback,
which is exactly the silent-revert vector the owner fears. This CLI is the
ONLY sanctioned path to a new ``universe_cache.json``:

    force build -> bounded guard -> atomic write; --commit is BEHIND the guard.

Guard (one governing family, C2-F3/F4 + F5 MC 10264; bound derived from the
SAME constants as the page ceiling so no blessed refresh can outgrow §9 #5,
and NO blessed shrink can ever walk under S0's REGISTRY_MIN):
    max(0.8 * current, min(current, REGISTRY_MIN))
        <=  m_unique_new  <=  min(1.5 * current, M_PAGE_MAX)
    named regions (usa, sweden, india, japan, hongkong): new >= 0.8 * current
    superset: new non-empty region set  ⊇  committed non-empty set (thin
              named-free regions cannot silently vanish; growth allowed)

Refuse => the tracked file is untouched (never left dirty — §9 S0 makes an
uncommitted refresh a pipeline abort). Commit+push wiring is the
orchestrator's (T4/T6); the code path exists behind --commit, tested only up
to the guard here.

Usage: python3 scripts/refresh_universe.py [--commit]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_universe as fu  # noqa: E402  (constants read at CALL time)

SHRINK_FLOOR = 0.8            # both sides' lower bound (same as S0's)
GROWTH_CEILING = 1.5          # overall upper bound (before M_PAGE_MAX)
NAMED_REGIONS = ("usa", "sweden", "india", "japan", "hongkong")
COMMIT_IDENTITY = ("code (MC 10221)", "code@agent-town.local")  # §10/§12


def _nonempty(buckets: dict[str, list[str]]) -> set[str]:
    return {r for r, v in buckets.items() if v}


def guard_refresh(committed: dict, new_buckets: dict[str, list[str]]
                  ) -> list[str]:
    """Pure guard over a candidate registry doc. Returns fail reasons ([] =
    blessed). ``committed`` is the current cache doc; ``new_buckets`` the
    force-built 'tickers' map."""
    reasons: list[str] = []
    cur_buckets = committed.get("tickers") or {}
    cur_view = fu.assign_primary_regions(cur_buckets)
    new_view = fu.assign_primary_regions(new_buckets)
    cur_m, new_m = fu.m_unique_of(cur_view), fu.m_unique_of(new_view)

    # F5 (MC 10264): the lower bound is RELATIVE (0.8 x current) AND absolute.
    # Two consecutive blessed shrinks each inside 0.8x walk the registry under
    # S0's absolute REGISTRY_MIN — blessed-then-red on the floor side. Bound:
    # lo = max(SHRINK_FLOOR x current, min(current, REGISTRY_MIN)) — at/above
    # the floor the absolute REGISTRY_MIN bites (no blessed refresh can sink
    # under the nightly); below it (bootstrap states only) the floor-protected
    # side is `current` itself: a sub-floor registry may never shrink further.
    floor_protected = min(cur_m, fu.REGISTRY_MIN)
    lo = max(SHRINK_FLOOR * cur_m, floor_protected)
    hi = min(GROWTH_CEILING * cur_m, fu.M_PAGE_MAX)
    if new_m < lo:
        reasons.append(f"m_unique {new_m} < shrink floor {lo:g} "
                       f"(max({SHRINK_FLOOR:g} x current {cur_m}, "
                       f"floor-protected {floor_protected} vs REGISTRY_MIN "
                       f"{fu.REGISTRY_MIN}))")
    if new_m > hi:
        reasons.append(f"m_unique {new_m} > upper bound {hi:g} "
                       f"= min({GROWTH_CEILING} x {cur_m}, M_PAGE_MAX "
                       f"{fu.M_PAGE_MAX})")
    for region in NAMED_REGIONS:
        cur_n, new_n = len(cur_buckets.get(region, [])), len(new_buckets.get(region, []))
        if cur_n and new_n < SHRINK_FLOOR * cur_n:
            reasons.append(f"named region {region} shrank: {new_n} < "
                           f"{SHRINK_FLOOR:g} x {cur_n}")
    vanished = _nonempty(cur_buckets) - _nonempty(new_buckets)
    if vanished:
        reasons.append("regions vanished (superset rule): "
                       + ", ".join(sorted(vanished)))
    return reasons


def _build_doc(new_buckets: dict[str, list[str]], now_iso: str) -> dict:
    return {"tickers": {r: list(v) for r, v in new_buckets.items()},
            "timestamp": now_iso, "version": fu.REGISTRY_VERSION}


def _atomic_write_json(doc: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def run_refresh(cache_path: Path | None = None, *, builder=None,
                commit: bool = False, now_iso: str | None = None) -> int:
    """Refresh once; returns exit code (0 blessed [+committed], 1 refused).

    ``builder`` is the seam for tests. Default (MC 10264 F2): a FORCED
    universe_builder build that writes NOTHING to disk — get_universe
    (force_refresh=True, write_cache=False), imported only when a build
    actually happens, never at module import. run_refresh is therefore the
    SOLE writer of the tracked file: guard first, then ONE atomic write."""
    path = Path(cache_path) if cache_path else fu.REGISTRY_PATH
    committed = json.loads(path.read_text(encoding="utf-8"))
    if builder is None:  # lazy: nightly/tests never import the builder
        from rocket.data.universe_builder import get_universe
        builder = lambda: get_universe(force_refresh=True, write_cache=False)
    new_buckets = builder()
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    reasons = guard_refresh(committed, new_buckets)
    if reasons:
        print("REFUSED — universe_cache.json untouched:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    _atomic_write_json(_build_doc(new_buckets, now_iso), path)
    print(f"blessed: m_unique {fu.m_unique_of(fu.assign_primary_regions(new_buckets))}"
          f" written to {path}")
    if commit:
        _commit(path)
    else:
        print("dry-run: file written, nothing committed (use --commit)")
    return 0


def _commit(path: Path) -> None:
    """Commit the blessed cache as its own commit (never leaves it dirty).
    Commit/push wiring is orchestrated per card; kept minimal here."""
    rel = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    subprocess.run(["git", "add", "--", rel], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "-c", f"user.name={COMMIT_IDENTITY[0]}",
         "-c", f"user.email={COMMIT_IDENTITY[1]}", "commit", "-m",
         "registry: guarded full-universe refresh (MC 10221)"],
        cwd=REPO_ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="commit the blessed cache (behind the guard)")
    args = ap.parse_args()
    sys.exit(run_refresh(commit=args.commit))


if __name__ == "__main__":
    main()
