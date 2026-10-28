"""B0 (MC 11545, D1/C1): regime index feed — the <=4 REGION_INDEX index CSVs
into the settled-bar store, REUSING the stage-1 walk (no second store path):
the plan is a tiny UniversePlan built from REGION_INDEX (one index ticker
per covered region), and the walk is fetch_store.run_fetch itself — typed
seam, single re-queue, store_io.upsert as the ONLY store writer, honest
failure (facts stay batch_completed=False, errors ride the caller's list;
a failed fetch never touches existing CSVs, never writes a partial one).
The wiring is INERT pre-B2: no caller supplies index_df until the wire
commit, so landing these CSVs cannot move today's scores (DA-c3 B.1).

run() takes the walk as a SEAM because generate_demo_page.py's stage-1
hunk passes its module-level fetch_store name — the suite's existing
monkeypatch of that name keeps the nightly wiring hermetic while
production gets the real walk. __main__ runs the same fetch standalone
(the allowed <=4-request live probe; --skip-fetch is NOT accepted here:
this bin IS the fetch). Batches: REGION_INDEX has 4 entries -> ONE
make_batches batch -> at most one bulk request per period bucket.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1]
for _p in (str(_SOURCE), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from full_universe import UniversePlan  # noqa: E402
from rocket.technical.regime import REGION_INDEX  # noqa: E402


def build_index_plan() -> UniversePlan:
    """REGION_INDEX inverted to the plan shape: region -> [its index].
    Region keys stay real (store_io.classify/upsert resolve their timezone
    for the settled-bar guard), and plan.order fixes the walk order."""
    return UniversePlan(regions={r: [t] for r, t in REGION_INDEX.items()},
                        order=list(REGION_INDEX), m_unique=len(REGION_INDEX))


def run(fetch_fn, *, skip_fetch, now_fn, errors=None, cache_dir=None,
        stats=None, writes_enabled=True) -> dict:
    """Walk build_index_plan() through the caller's fetch seam (the real
    fetch_store.run_fetch on the nightly; the patched name in tests).
    Returns {index_ticker: {"batch_completed", "typed"}} — same facts
    shape as stage 1's bars, keyed by the index tickers only."""
    return fetch_fn(build_index_plan(), skip_fetch=skip_fetch, now_fn=now_fn,
                    errors=errors, cache_dir=cache_dir, stats=stats,
                    writes_enabled=writes_enabled)


def main() -> None:
    import fetch_store
    now = datetime.now(timezone.utc)
    errors: list[str] = []
    facts = run(fetch_store.run_fetch, skip_fetch=False, now_fn=lambda: now,
                errors=errors)
    landed = sum(1 for f in facts.values() if f["batch_completed"])
    print(f"INDEX-FETCH landed={landed}/{len(facts)} errors={len(errors)}")
    for t, f in facts.items():
        print(f"  {t}: batch_completed={f['batch_completed']} typed={f['typed']}")
    for e in errors:
        print(f"  error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
