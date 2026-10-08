"""Shared hermetic fixtures for the MC 10222 registry/store/compute tests.

NOT a test module (no test_ prefix). Synthetic registry per DESIGN §9 G4:
>= 5 regions incl. one named ``hongkong`` with > 10 members, a 6th region
(``finland``) the fetch stub never returns, ``international`` exclusive
members (okontrollerat drop), an empty bucket (``china``), version 2, and a
59-run ALL-numeric block aligned to a 50-boundary in loader order — the real
C3-F1 pathology (the live registry's longest junk run is 59). Every date in
every fixture is RUN-RELATIVE (C3-F3d): built off ``date.today()``, never
absolute pins.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
SCRIPTS = SOURCE / "scripts"
for _p in (str(SCRIPTS), str(SOURCE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

JUNK_RUN = 59                       # VERIFIED max run in the live registry
BATCH = 50                          # bulk_fetcher.BATCH_SIZE
FIXTURE_M_UNIQUE = 222
UNFETCHED_REGION = "finland"        # the 6th region; stubs never return it

# 50 live + 59 aligned-junk + 11 live == 120, junk starts at flat index 50
USA = [f"USA{i:03d}" for i in range(1, 51)]
JUNK = [str(1965 + i) for i in range(JUNK_RUN)]
USA_TAIL = [f"USA{i:03d}" for i in range(51, 62)]

FIXTURE_BUCKETS: dict[str, list[str]] = {
    "usa": USA + JUNK + USA_TAIL,
    "sweden": [f"SWE{i:02d}" for i in range(1, 41)],
    "germany": [f"GER{i:02d}" for i in range(1, 11)],
    "india": [f"IND{i:02d}" for i in range(1, 31)],
    "hongkong": [f"HKG{i:02d}" for i in range(1, 17)],   # >10 members, named
    "finland": [f"FIN{i:02d}" for i in range(1, 7)],     # never fetched
    "china": [],                                        # empty -> no tab
    "international": (USA[:20] + [f"SWE{i:02d}" for i in range(1, 21)]
                      + ["INTL-EXCL-1", "INTL-EXCL-2"]),
}
FIXTURE_ORDER = ["usa", "sweden", "germany", "india", "hongkong", "finland"]
FIXTURE_SIZES = {"usa": 120, "sweden": 40, "germany": 10, "india": 30,
                 "hongkong": 16, "finland": 6}
FIXTURE_TS = "2026-09-13T17:33:25.510914+00:00"
INTL_ONLY = ["INTL-EXCL-1", "INTL-EXCL-2"]


def fixture_doc() -> dict:
    return {"tickers": {k: list(v) for k, v in FIXTURE_BUCKETS.items()},
            "timestamp": FIXTURE_TS, "version": 2}


def write_fixture_registry(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture_doc(), indent=1), encoding="utf-8")
    return path


def flat_plan_order(plan) -> list[str]:
    """The pipeline's loader-order list of every assigned ticker (batching
    input): plan.order regions, cache order within each."""
    return [t for r in plan.order for t in plan.regions[r]]


def is_junk(ticker: str) -> bool:
    return ticker.isdigit()


def contiguous_batches(items: list[str], size: int = BATCH) -> list[list[str]]:
    """THE RETIRED, BUGGY BATCHER (DA-verdict-c3 C3-F1) — lives ONLY here, so
    the planted-bad red run can demonstrate the pathology the production
    stride batcher removes. Never import this from scripts/."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def fetch_stub(*, dead_regions=(), all_dead=False):
    """_fetch_batch double: {} when a batch touches a dead region or every
    member is numeric junk; else a completed dict of live members only
    (numeric junk absent -> §4 #4 dead_at_fetch, VERIFIED control: a mixed
    batch with 49 junk + 1 live completes)."""
    def _fetch(batch: list[str], period: str) -> dict:
        if all_dead:
            return {}
        if dead_regions and any(_in_region(t, dead_regions) for t in batch):
            return {}
        if all(is_junk(t) for t in batch):
            return {}
        return {t: f"df-{t}" for t in batch if not is_junk(t)}
    return _fetch


def _in_region(t: str, dead_regions) -> bool:
    return any(t in FIXTURE_BUCKETS.get(r, ()) for r in dead_regions)


# ── frames: RUN-RELATIVE dates only (C3-F3d) ───────────────────────────────
def today() -> date:
    return date.today()


def mk_frame(days: int = 60, end: date | None = None, start_price=10.0,
             jitter_every: int = 7) -> pd.DataFrame:
    """Synthetic daily OHLCV, lowercase columns + ISO date column, ending at
    ``end`` (default run date), gently rising with mild periodic jitter."""
    end = end or today()
    dates = pd.date_range(end=pd.Timestamp(end), periods=days, freq="D")
    closes, price = [], start_price
    for i in range(days):
        price = price * 1.004 + (0.25 if jitter_every and i % jitter_every == 0 else 0)
        closes.append(round(price, 4))
    return pd.DataFrame({
        "date": dates, "open": closes,
        "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": [1_000_000.0] * days})


def fixed_utc(day: date, at=time(5, 0)):
    """Frozen clock seam: 05:00 UTC on run-relative ``day`` (nightly time)."""
    def _now() -> datetime:
        return datetime.combine(day, at, tzinfo=timezone.utc)
    return _now


def local_evening_utc(day: date, region: str):
    """Frozen clock seam: 20:00 LOCAL on ``day`` for ``region`` (past
    SESSION_CLOSE_GUARD) returned as a UTC-aware callable — DST-safe by
    construction (zoneinfo does the offset math)."""
    from zoneinfo import ZoneInfo
    from rocket.data.universe_regions import REGION_META
    tz = REGION_META.get(region, {}).get("timezone") or "UTC"
    instant = datetime.combine(day, time(20, 0),
                               tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    return lambda: instant


def day_delta(offset_days: int) -> date:
    return today() + timedelta(days=offset_days)
