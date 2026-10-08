# reason: generator stage 1 (fetch/backfill, DESIGN §5) — extracted verbatim
# from generate_demo_page.py at MC 10309 (the generator hit the 400-line
# ceiling); this module owns the fetch walk only.
"""DESIGN §5. Returns facts {ticker: {"batch_completed": bool}} for the §4
partition. Batches = full_universe.make_batches STRIDE walk (C3-F1: never
contiguous windows). A {} batch re-queues ONCE at end of stage; still {}
-> members stay batch_completed=False (loud not_fetched) + stderr
BATCH-EMPTY-OR-DEAD (§4 #1: {} is not delisting evidence). Split-break
deltas re-fetch once as 1y replace (§5 3b). Failed fetches never touch
existing CSVs."""
from __future__ import annotations

import sys
import time


def run_fetch(plan, *, skip_fetch, now_fn, cache_dir=None, fetcher=None,
              errors=None) -> dict:
    facts = {t: {"batch_completed": False}
             for r in plan.order for t in plan.regions.get(r, [])}
    if skip_fetch or not facts:
        return facts
    errors = errors if errors is not None else []
    import full_universe
    import store_io
    if fetcher is None:
        from rocket.data.bulk_fetcher import _fetch_batch as fetcher
    from rocket.data.bulk_fetcher import BATCH_DELAY
    region_of = {t: r for r in plan.order for t in plan.regions.get(r, [])}
    today = now_fn().date()
    buckets: dict[str, list[str]] = {}
    for t, r in region_of.items():
        period, _, _ = store_io.classify(t, r, today, cache_dir=cache_dir)
        buckets.setdefault(period, []).append(t)
    requeue: list[tuple[list[str], str]] = []
    split_requeue: list[str] = []

    def absorb(batch, res, *, backfill: bool) -> None:
        for t in batch:
            facts[t]["batch_completed"] = True
        for t, frame in res.items():
            try:
                if not backfill:
                    stored = store_io.read_store(t, region_of[t],
                                                 cache_dir=cache_dir)
                    if store_io.split_break_detected(stored, frame):
                        split_requeue.append(t)
                        continue
                store_io.upsert(t, region_of[t], frame, cache_dir=cache_dir,
                                replace=backfill, now_fn=now_fn)
            except Exception as exc:  # noqa: BLE001 — store death != stage death
                errors.append(f"{t}: store: {exc}")

    def empty_note(batch) -> None:
        print(f"BATCH-EMPTY-OR-DEAD batch={','.join(batch[:3])} n={len(batch)}",
              file=sys.stderr)

    for period, tickers in sorted(buckets.items()):
        backfill = period == store_io.PERIOD_BACKFILL
        for batch in full_universe.make_batches(tickers):
            try:
                res = fetcher(batch, period)
            except Exception as exc:  # noqa: BLE001 — one batch must not kill the stage
                res = {}
                errors.append(f"batch[{','.join(batch[:3])}…]: {exc}")
            if res:
                absorb(batch, res, backfill=backfill)
            else:
                requeue.append((batch, period))
            time.sleep(BATCH_DELAY)
    for batch, period in requeue:            # ONE end-of-stage re-queue (§5)
        try:
            res = fetcher(batch, period)
        except Exception:  # noqa: BLE001
            res = {}
        if res:
            absorb(batch, res, backfill=period == store_io.PERIOD_BACKFILL)
        else:
            empty_note(batch)
    if split_requeue:                        # §5 3b: full 1y replaces the file
        for batch in full_universe.make_batches(sorted(split_requeue)):
            try:
                res = fetcher(batch, store_io.PERIOD_BACKFILL)
            except (Exception, SystemExit) as exc:      # T13 F7: ANY abort or
                print(f"FAIL-CLOSED — split-requeue-fetch dog: {exc}",
                      file=sys.stderr)          # raise is systemd-greppable
                sys.exit(1)
            if res:
                absorb(batch, res, backfill=True)
            else:
                empty_note(batch)
    return facts
