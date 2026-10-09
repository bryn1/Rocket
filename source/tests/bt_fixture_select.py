"""Deterministic selector for the FROZEN bt_fixture300 campaign store (T-BT2).

One concern: the pure selection function. The 300 CSVs themselves are committed
DATA in ``tests/data/bt_fixture300/`` (byte-locked by its SHA256SUMS, golden35
idiom) — this module exists so a reviewer can REPRODUCE the frozen names from
committed inputs and nothing else:

* ``tests/data/bt_fixture300_candidates.csv`` — the build-time store listing
  ``name,region,n_bars`` (registry-backed files only, sorted). Region is the
  ``full_universe.load_plan().regions`` primary region of the build day
  (2026-10-09, registry_ts 2026-10-08T20:17:28Z); n_bars the stored data rows.
  Recording them once is what makes reproduction independent of the live store
  (the nightly rewrite moves bar counts daily — see MC 10304's lesson in
  test_golden35_fixtures.py).
* ``FIXTURE_SEED = 20261009`` — the fixed seed; the per-name rank is
  ``sha256(f"{seed}\\x1f{name}")``, so selection is a deterministic function of
  ticker name + seed, with no RNG stream order to keep stable.

Stratification (PLAN-c3 §6 T-BT2: 15 regions present, bar-count spread incl.
thin files so the §3 bar gate is testable): region quotas mirror
``sample_backtest.stratified_sample`` (max(1, round(total * n_r / n)) with the
hard cap shaving the largest quota first), and WITHIN a region the quota is
split over three bar-count buckets — thin (< 60 = WARMUP_BARS), mid (< 200),
tall (>= 200) — reserving a fifth of each region's quota per minority bucket
where such files exist (roll-over fills the remainder, tall first). Picks
inside a bucket are by rank ascending.

Rebuild recipe (documented, needs the live store; NOT run by tests): select
from the CURRENT listing, copy those CSVs byte-for-byte out of
``source/data/raw`` (never fetch), and regenerate SHA256SUMS — the byte-lock
test then proves the committed bytes still match the committed manifest.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

FIXTURE_SEED = 20261009
FIXTURE_SIZE = 300
THIN_BARS = 60          # < indicator_eval.WARMUP_BARS — the §3 bar-gate class
MID_BARS = 200          # below the mature-store envelope (≈ 251 bars today)

FIXTURE_DIR = Path(__file__).resolve().parent / "data" / "bt_fixture300"
CANDIDATES_CSV = (Path(__file__).resolve().parent / "data"
                  / "bt_fixture300_candidates.csv")


def candidate_rank(name: str, seed: int = FIXTURE_SEED) -> str:
    """Stable per-name rank: same (name, seed) -> same rank, every run."""
    return hashlib.sha256(f"{seed}\x1f{name}".encode()).hexdigest()


def _bucket(n_bars: int) -> str:
    return "thin" if n_bars < THIN_BARS else "mid" if n_bars < MID_BARS else "tall"


def region_quotas(counts: dict[str, int], total: int) -> dict[str, int]:
    """sample_backtest idiom: max(1, round(total*n_r/n)), hard cap shaving the
    largest quota first (ties by region name desc), exact-total top-up growing
    the largest remainder first. Pure; >= total regions -> every one >= 1 seat."""
    n = sum(counts.values())
    quotas = {r: max(1, round(total * c / n)) for r, c in counts.items() if c}
    while sum(quotas.values()) > total:      # hard cap: shave largest first,
        top = max(quotas, key=lambda r: (quotas[r], r))   # ties by region name
        quotas[top] -= 1
    while sum(quotas.values()) < total:      # top-up: largest fair-share
        order = sorted(quotas, key=lambda r: (           # deficit first, ties
                -(total * counts[r] / n - quotas[r]), r))  # by region name
        quotas[order[0]] += 1
    return quotas


def _bucket_quota(quota: int, avail: dict[str, int]) -> dict[str, int]:
    """A fifth of the region quota per minority bucket where available;
    remainder rolls in deterministic order: tall, thin, mid."""
    take = {b: min(avail.get(b, 0), quota // 5) for b in ("thin", "mid")}
    left = quota - sum(take.values())
    take["tall"] = 0
    for b in ("tall", "thin", "mid"):
        add = min(avail.get(b, 0) - take[b], left)
        take[b] += add
        left -= add
    return take


def select_fixture300(candidates, *, seed: int = FIXTURE_SEED,
                      total: int = FIXTURE_SIZE) -> list[str]:
    """candidates: iterable of (name, region, n_bars) for registry-backed
    store files. Returns the SELECTED csv names, sorted. Pure: same input
    (in any order) -> identical output; no RNG stream, rank is per name."""
    by_region: dict[str, list] = defaultdict(list)
    for name, region, n_bars in candidates:
        by_region[region].append((candidate_rank(name, seed), name,
                                  _bucket(int(n_bars))))
    quotas = region_quotas({r: len(v) for r, v in by_region.items()}, total)
    picked: list[str] = []
    for region in sorted(by_region):
        groups: dict[str, list] = defaultdict(list)
        for rank, name, bucket in by_region[region]:
            groups[bucket].append((rank, name))
        for bucket in groups:
            groups[bucket].sort()                       # rank order, stable
        take = _bucket_quota(quotas[region],
                             {b: len(groups.get(b, [])) for b in
                              ("thin", "mid", "tall")})
        for bucket in ("thin", "mid", "tall"):
            picked += [name for _, name in groups.get(bucket, [])[:take[bucket]]]
    return sorted(picked)


def load_candidates(path: Path = CANDIDATES_CSV) -> list[tuple[str, str, int]]:
    """Read the committed build listing sidecar (name, region, n_bars)."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        name, region, n_bars = line.split(",")
        out.append((name, region, int(n_bars)))
    return out
