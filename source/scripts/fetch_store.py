# reason: generator stage 1 (fetch/backfill, DESIGN §5) — extracted verbatim
# from generate_demo_page.py at MC 10309 (the generator hit the 400-line
# ceiling); this module owns the fetch walk only.
"""DESIGN §5. Returns facts {ticker: {"batch_completed": bool}} for the §4
partition. Batches = full_universe.make_batches STRIDE walk (C3-F1: never
contiguous windows). A {} batch re-queues ONCE at end of stage; still {}
-> members stay batch_completed=False (loud not_fetched) + stderr
BATCH-EMPTY-OR-DEAD (§4 #1: {} is not delisting evidence). STARVED members
(F-2, MC 10309): batch_completed is set ONLY for members the fetcher
actually returned; members of a NON-empty batch that lack their own frame
join the SAME single re-queue round as their own mini-batches and after it
stay False -> honest NOT_FETCHED + stderr STARVED-NOT-FETCHED (partial
starvation is the {} disease at member granularity). Split-break deltas
re-fetch once as 1y replace (§5 3b). Failed fetches never touch existing
CSVs.

Rate-limit backoff (F-1, MC 10309 — runtime FAIL 2026-10-08: 241
YFRateLimitError, 2,593/12,793 landed): each CONSECUTIVE rate-limit-signature
batch exception sleeps the next RL_LADDER rung instead of BATCH_DELAY
(event #k -> rung k: 1, 2, 4, 8, then 10 s cap; rung 0.5 IS the clean
BATCH_DELAY entry); RL_RESET_AFTER consecutive clean batches reset the
ladder (the 5-clean reset is the decay rule — a lone clean batch neither
sleeps the ladder nor erases the streak). If ANY event was seen, one
COOLDOWN sleep runs before the re-queue round — unchanged, exactly once.
That round is paced by the SAME helper and the SAME ladder state (T20,
MC 10311: it is the same walk, and its ~512 back-to-back calls were the
ones most likely to 429). Budget arithmetic, SLEEP at its worst (every
batch of BOTH rounds throttled): 512 walk x 10 s cap + 60 s cooldown +
<=512 re-queue x 10 s cap = 10,300 s < 10,800 s TimeoutStartSec of
rocket-demo-publish.service (the round cannot exceed the walk's batch
count: batches partition the members and the starved re-batch at the same
size). The two per-call maxima cannot coincide: the
measured 2.1-3.5 s belongs to a SUCCESSFUL batch, which by definition
paces at BATCH_DELAY (all-clean bound ~1,024 x (0.5 + 3.5) = ~4,100 s),
while a throttled call is a fast 429 raise. Honest residual: the
all-throttled bound leaves ~500 s for ~1,024 raises (~0.5 s each), so a
slow-raising Yahoo could cross it — the unit timeout, not this math,
bounds that night (which lands nothing anyway)."""
from __future__ import annotations

import sys
import time

RL_LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 10.0)   # event #k -> rung k (cap last)
RL_RESET_AFTER = 5                             # consecutive clean batches
COOLDOWN = 60.0                                # once, before the re-queue
_RL_MARKS = ("429", "ratelimit", "too many requests")


def is_rate_limit(exc: BaseException) -> bool:
    """F-1 signature: YFRateLimitError & friends, by CLASS NAME or message.
    Generic exceptions stay generic in `errors` — this only drives backoff."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _RL_MARKS)


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
    # F-2: members whose frame was absent from a NON-empty batch, per period.
    # Collected in the main walk ONLY (members that re-starve in the re-queue
    # round are already in here — that round is the last one, §5).
    starved: dict[str, list[str]] = {}
    rl_events = 0                    # rate-limit events seen this run (F-1)
    rl_streak = 0                    # consecutive rate-limit batches
    clean_streak = 0                 # consecutive clean batches (ladder decay)

    def absorb(batch, res, *, backfill: bool,
               starve_into: list[str] | None = None) -> None:
        for t in batch:
            if t in res:
                facts[t]["batch_completed"] = True
            elif starve_into is not None:
                starve_into.append(t)  # joined a completed batch, no frame
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

    def pace(rate_limited: bool) -> None:
        """ONE pacing rule for the WHOLE stage (T20, MC 10311): the main walk
        and the end-of-stage re-queue round are one walk, so they share this
        helper and the rl_* state behind it — clean batch -> BATCH_DELAY,
        event -> next ladder rung; a rung reached in the round continues the
        streak the main walk left."""
        nonlocal rl_events, rl_streak, clean_streak
        if rate_limited:                       # F-1 ladder (module docstring)
            rl_events += 1
            rl_streak += 1
            clean_streak = 0
            delay = RL_LADDER[min(rl_streak, len(RL_LADDER) - 1)]
        else:
            clean_streak += 1
            if clean_streak >= RL_RESET_AFTER:
                rl_streak = 0                  # reset to BATCH_DELAY pace
            delay = BATCH_DELAY
        time.sleep(delay)

    for period, tickers in sorted(buckets.items()):
        backfill = period == store_io.PERIOD_BACKFILL
        starve_into = starved.setdefault(period, [])
        for batch in full_universe.make_batches(tickers):
            rate_limited = False
            try:
                res = fetcher(batch, period)
            except Exception as exc:  # noqa: BLE001 — one batch must not kill the stage
                res = {}
                rate_limited = is_rate_limit(exc)   # classify BEFORE errors ride
                errors.append(f"batch[{','.join(batch[:3])}…]: {exc}")
            if res:
                absorb(batch, res, backfill=backfill, starve_into=starve_into)
            else:
                requeue.append((batch, period))
            pace(rate_limited)
    for period, members in sorted(starved.items()):    # F-2: starved members
        # re-queue as their OWN mini-batches — the SAME single round (§5),
        # never a second one; what survives it fileless is honest NOT_FETCHED.
        rest = [t for t in members if not facts[t]["batch_completed"]]
        for batch in full_universe.make_batches(sorted(rest)):
            requeue.append((batch, period))
    if rl_events:                                  # ONE cooldown before re-queue
        time.sleep(COOLDOWN)
    for batch, period in requeue:            # ONE end-of-stage re-queue (§5)
        rate_limited = False
        try:
            res = fetcher(batch, period)
        except Exception as exc:  # noqa: BLE001
            res = {}
            # T20: paced by the SAME helper/state as the walk — this round is
            # the one most likely to 429, so an event here steps the ladder
            # from the rung the walk stopped at (reporting path unchanged).
            rate_limited = is_rate_limit(exc)
        if res:
            absorb(batch, res, backfill=period == store_io.PERIOD_BACKFILL,
                   starve_into=starved.setdefault(period, []))
        else:
            empty_note(batch)
        pace(rate_limited)
    still = list(dict.fromkeys(                        # F-2 loud, systemd-
        t for members in starved.values() for t in members  # greppable; dedup:
        if not facts[t]["batch_completed"]))    # re-starve may repeat a member
    if still:
        print(f"STARVED-NOT-FETCHED n={len(still)}", file=sys.stderr)
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
