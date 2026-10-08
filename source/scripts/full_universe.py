"""Full-universe registry loader + the ONE governing-constants home (MC 10222).

Reads ``source/rocket/data/universe_cache.json`` and emits a deterministic
``UniversePlan`` (DESIGN §4): primary-region assignment by frozen bucket
priority, tab order (usa, sweden, germany first, then descending size),
REGION_META labels, anti-revert floors (version==2, m_unique >= REGISTRY_MIN,
>= MIN_REGIONS non-empty, tracked-cache clean, optional anchor-shrink) that
ABORT BEFORE ANY NETWORK — this module performs none (no fetch import).

Owns seam C4 (``CACHE_DIR`` + ``cache_filename``, escape set extended to
``. - /`` -> ``_``) and the ONE constants home for the HTML budget
(MAX_HTML_BYTES / CHROME_EST / BYTES_PER_ROW_EST / ALLA_CAP -> M_PAGE_MAX);
``refresh_universe.py`` derives its upper bound from the SAME numbers
(DESIGN §9 #5 / §10, C2-F3). Fetch batching is a deterministic stride
(round-robin) walk — NEVER contiguous windows (DA-verdict-c3 C3-F1: the real
registry carries a 59-long all-numeric junk run that a contiguous 50-batch
would hit 100 % and make 50 tickers permanently ``not_fetched``).

Replaces demo_universe.py (R1); demo_universe is deleted by the T4 wiring.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "source") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "source"))

from rocket.data.universe_regions import REGION_META  # noqa: E402

REGISTRY_PATH = REPO_ROOT / "source" / "rocket" / "data" / "universe_cache.json"

# ── anti-revert floors (DESIGN §9 S0) ──────────────────────────────────────
REGISTRY_VERSION = 2
REGISTRY_MIN = 10_000          # m_unique floor, aborts before any network
MIN_REGIONS = 8                # non-empty buckets required
SHRINK_FACTOR = 0.8            # m_unique >= 0.8 * anchor (C2-F5)

# ── ONE constants home for the page budget (DESIGN §9 #5, C2-F3) ───────────
MAX_HTML_BYTES = 2_500_000
CHROME_EST = 8_000
BYTES_PER_ROW_EST = 150        # conservative over measured 137-143 B/row
ALLA_CAP = 500
M_PAGE_MAX = (MAX_HTML_BYTES - CHROME_EST) // BYTES_PER_ROW_EST - ALLA_CAP

# ── seam C4 (moved from demo_universe.py; escape set extended, F9) ─────────
CACHE_DIR = REPO_ROOT / "source" / "data" / "raw"


def cache_filename(ticker: str) -> str:
    """Ticker -> cache CSV stem; '/' escapes too (BRK/A, BF/B, ...)."""
    return (ticker.replace(".", "_").replace("-", "_")
            .replace("/", "_") + ".csv")


# Frozen bucket priority for PRIMARY-region assignment (DESIGN §4). A future
# cache key not listed here appends last (sorted) and its tab appears
# automatically. 'international' is never primary; its exclusive members land
# in OKONTROLLERAT_KEY -> dropped (never a tab, never in order, m_unique).
PRIMARY_PRIORITY = (
    "usa", "sweden", "germany", "france", "switzerland", "uk", "norway",
    "denmark", "finland", "canada", "australia", "japan", "hongkong",
    "china", "india", "korea", "singapore", "brazil",
)
INTERNATIONAL_KEY = "international"
OKONTROLLERAT_KEY = "okontrollerat"
FACE_FIRST = ("usa", "sweden", "germany")  # today's face leads the tab order


class RegistryError(RuntimeError):
    """S0 floor violated; the pipeline must abort before any network call."""


def _bucket_priority(keys: set[str]) -> dict[str, int]:
    known = {k: i for i, k in enumerate(PRIMARY_PRIORITY)}
    extra = sorted(keys - set(known) - {INTERNATIONAL_KEY})
    for i, key in enumerate(extra):
        known[key] = len(PRIMARY_PRIORITY) + i
    return known


def region_label(region: str) -> str:
    """Swedish label from REGION_META; an unknown future key -> the key."""
    return REGION_META.get(region, {}).get("label") or region


def assign_primary_regions(buckets: dict[str, list[str]]) -> dict[str, dict]:
    """Pure function: cache 'tickers' map -> assignment view.

    Returns {primary_region: [members in cache order]} plus
    OKONTROLLERAT_KEY: international-only members (sorted). Dedup: a ticker's
    primary region is its highest-priority bucket (first match, bucket scan in
    priority order). 'international' is never primary.
    """
    priority = _bucket_priority(set(buckets))
    assigned: dict[str, list[str]] = {}
    seen: set[str] = set()
    for region in sorted((r for r in buckets if r != INTERNATIONAL_KEY),
                         key=lambda r: priority[r]):
        kept = [t for t in buckets.get(region, []) if not (t in seen or seen.add(t))]
        if kept:
            assigned.setdefault(region, []).extend(kept)
    others = set().union(*(set(v) for k, v in buckets.items()
                           if k != INTERNATIONAL_KEY)) if buckets else set()
    intl_only = sorted(set(buckets.get(INTERNATIONAL_KEY, [])) - others)
    if intl_only:
        assigned[OKONTROLLERAT_KEY] = intl_only
    return assigned


def tab_order(assigned: dict[str, list[str]]) -> list[str]:
    """usa, sweden, germany first (FACE_FIRST, if non-empty), then remaining
    non-empty regions by descending size; deterministic ties via bucket
    priority. Empty buckets produce no tab; okontrollerat never a tab."""
    priority = _bucket_priority(set(assigned))
    body = [r for r in assigned if r not in (INTERNATIONAL_KEY, OKONTROLLERAT_KEY)]
    head = [r for r in FACE_FIRST if r in body]
    rest = sorted((r for r in body if r not in head),
                  key=lambda r: (-len(assigned[r]), priority[r]))
    return head + rest


@dataclass
class UniversePlan:
    """Loader output (DESIGN §3/§4): the only universe the pipeline may use."""
    regions: dict[str, list[str]] = field(default_factory=dict)  # primary tabs
    order: list[str] = field(default_factory=list)
    m_unique: int = 0
    dropped: list[str] = field(default_factory=list)  # international-only
    registry_ts: str = ""
    okontrollerat: list[str] = field(default_factory=list)

    def label(self, region: str) -> str:
        return region_label(region)


def m_unique_of(assigned: dict[str, list[str]]) -> int:
    """Assigned count: everything except okontrollerat/international."""
    return sum(len(v) for k, v in assigned.items()
               if k not in (OKONTROLLERAT_KEY, INTERNATIONAL_KEY))


def load_plan(cache_path: Path | None = None, *,
              anchor_m_unique: int | None = None,
              git_check: bool = True) -> UniversePlan:
    """Read + validate the registry; EVERY floor raises RegistryError BEFORE
    any network (this module performs none). ``anchor_m_unique`` is the
    committed indicator_stats.json "registry" anchor (C2-F5); absent/None ->
    shrink check passes (C3-F3c bootstrap)."""
    path = Path(cache_path) if cache_path else REGISTRY_PATH
    if git_check:
        _assert_cache_clean(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"registry unreadable: {path}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("version") != REGISTRY_VERSION:
        raise RegistryError(
            f"registry version {doc.get('version')!r} != {REGISTRY_VERSION}"
            if isinstance(doc, dict) else "registry is not an object")
    buckets = doc.get("tickers") or {}
    assigned = assign_primary_regions(buckets)
    m_unique = m_unique_of(assigned)
    non_empty = sum(1 for r, v in buckets.items()
                    if r != INTERNATIONAL_KEY and v)
    if m_unique < REGISTRY_MIN:
        raise RegistryError(
            f"registry m_unique {m_unique} < REGISTRY_MIN {REGISTRY_MIN}")
    if non_empty < MIN_REGIONS:
        raise RegistryError(
            f"registry has {non_empty} non-empty regions < {MIN_REGIONS}")
    if anchor_m_unique and m_unique < SHRINK_FACTOR * anchor_m_unique:
        raise RegistryError(
            f"registry shrink: m_unique {m_unique} < {SHRINK_FACTOR:g} x "
            f"anchor {anchor_m_unique}")
    regions = {k: list(v) for k, v in assigned.items()
               if k not in (OKONTROLLERAT_KEY, INTERNATIONAL_KEY)}
    dropped = list(assigned.get(OKONTROLLERAT_KEY, []))
    return UniversePlan(regions=regions, order=tab_order(assigned),
                        m_unique=m_unique, dropped=dropped,
                        registry_ts=str(doc.get("timestamp", "")),
                        okontrollerat=dropped)


def _assert_cache_clean(path: Path) -> None:
    """S0: an uncommitted hand-trim of the tracked cache aborts pre-fetch.
    Skipped outside a git checkout or for paths git does not see (fixtures)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--",
             str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT)
                 else path)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return
    if proc.returncode != 0:
        return  # not a git checkout (or path outside it): check not applicable
    if proc.stdout.strip():
        raise RegistryError(
            f"universe_cache.json dirty — uncommitted registry edit "
            f"(refresh via scripts/refresh_universe.py, commit first)")


# ── deterministic fetch batching (C3-F1 + T13 F3/F4, MC 10264) ──────────────
BATCH_SIZE = 50                 # == bulk_fetcher.BATCH_SIZE (kept in sync here
                                # so the loader stays import-free of yfinance)


def make_batches(tickers: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """Deterministic STRIDE batching by BATCH COUNT (T13 F3, ARCH T10 F-1):
    n = ceil(len/size) batches, batch j = items[j::n] — every batch carries
    <= size members, so at the live 12,793 registry the walk is 256 batches
    of max 50, exactly the DESIGN §5 / ARCHITECTURE.md:43 unit the measured
    2.1-3.5 s per-batch and TimeoutStartSec math were timed on (the retired
    items[j::size] form shipped 50 batches of 256 — one dead yf.download =
    256 not_fetched). Never a contiguous window (C3-F1).

    Digit-only-looking tickers are spread round-robin on top of the walk
    (T13 F4): every batch carries <= ceil(junk/n) of them, so while the
    registry holds no more digit-junk than batches (live: 79 << 256) NO batch
    can carry more than one — BOUNDED BY CONSTRUCTION for the digit-junk
    class, with a ValueError if the distributor itself ever breaks the cap.
    Honest limit: other all-dead orderings are NOT disproved — a batch of
    fetch-dead live tickers still fails closed via not_fetched -> §9 #1 floor.
    Pure function (identical input -> identical output, no RNG)."""
    if size <= 0:
        raise ValueError(f"batch size must be positive, got {size}")
    n = -(-len(tickers) // size)                       # ceil(len/size) batches
    batches = [list(tickers[j::n]) for j in range(n)]
    _spread_digit_junk(batches)
    return batches


def _spread_digit_junk(batches: list[list[str]]) -> None:
    """In-place: rebalance digit-only-looking members so each batch carries
    <= ceil(total/len(batches)) of them. Swaps only — batch sizes, the
    partition and the walk's determinism are preserved; deterministic order
    (sources/targets by index)."""
    if not batches:
        return
    junk_per = [sum(1 for t in b if t.isdigit()) for b in batches]
    cap = -(-sum(junk_per) // len(batches))            # ceil(junk/batch_count)
    if max(junk_per) <= cap:
        return
    for src in range(len(batches)):
        while junk_per[src] > cap:
            tgt = next((k for k in range(len(batches))
                        if k != src and junk_per[k] < cap
                        and any(not t.isdigit() for t in batches[k])), None)
            if tgt is None:                            # distributor stuck —
                break                                  # caught by the check
            i = next(i for i, t in enumerate(batches[src]) if t.isdigit())
            j = next(j for j, t in enumerate(batches[tgt])
                     if not t.isdigit())
            batches[src][i], batches[tgt][j] = batches[tgt][j], batches[src][i]
            junk_per[src] -= 1
            junk_per[tgt] += 1
    for k, count in enumerate(junk_per):
        if count > cap:
            raise ValueError(
                f"digit-junk bound violated: batch {k} carries {count} > cap "
                f"{cap} of {sum(junk_per)} digit-only members over "
                f"{len(batches)} batches — registry too junk-dense to batch "
                f"safely (fail closed, not stacked)")
