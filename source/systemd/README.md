# Rocket Stock Scanner - Systemd Services

## The ONLY live pair: rocket-demo-publish.timer + .service

The live product has exactly ONE scheduled unit pair — `rocket-demo-publish.timer` +
`rocket-demo-publish.service`. Installed, enabled and verified as user units on the
deploy host's `~/.config/systemd/user/` (checked 2026-10-24, MC 11200: the only Rocket
units installed there are this pair).

> The four retired chat-bot-era units `daily-scan.service/.timer` and
> `portfolio-check.service/.timer` were deleted from this directory by the hygiene
> card (MC 11199 batch B1, 2026-10-24): their `ExecStart` paths pointed at a host
> layout that no longer exists and at scripts retired with them, so a host rebuilt
> from them would have run two phantom cron services of a retired product. They
> were NOT installed anywhere on the deploy host (checked 2026-10-24, MC 11200).
> Nothing else lives in this directory: `rocket-demo-publish` is the only unit pair.

## rocket-demo-publish — the ONE nightly publish pipeline

This is the only pipeline that publishes the demo page. There is deliberately **no second
timer** for the backtest: two timers on one repo race the same working tree (the publish
step does git pull/rebase), so the backtest runs inside the same nightly service.

Install (repo copies are the source of truth; copy both files, do not hand-edit the
installed ones):

```bash
cp rocket-demo-publish.service rocket-demo-publish.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rocket-demo-publish.timer
```

### Pipeline, end to end

```
rocket-demo-publish.timer   (user unit, OnCalendar 05:00 UTC, Persistent=true →
      │                      runs catch up after downtime)
      ▼
rocket-demo-publish.service (Type=oneshot, TimeoutStartSec=14400 — full-universe nightly,
      │                      re-derived MC 10220; the 35-era 1800 would kill the full pass.
      │                      Owner ruling D4 (MC 11474): 10800 → 14400 (4 h) RAISED by the
      │                      B4 card (this repo edit); the host re-install read-back leg
      │                      (systemctl --user show … -p TimeoutStartUSec = 14400s) is the
      │                      orchestrator's named post-land step.)
      ▼
source/scripts/generate_demo_page.py
      ├─ entry flock on .demo-publish.lock (store root): a second concurrent run is
      │  refused loudly with zero writes (MC 11187); --dry-run is READ-ONLY vs the store
      ├─ loads the tracked registry (full_universe; charset gate + floors fire BEFORE any
      │  network) — the FULL universe (counts move only via refresh_universe.py; the
      │  current registry epoch is recorded, dated, in LEDGER.md) — then batched
      │  delta/backfill OHLCV fetch through the store (store_io + bulk_fetcher),
      │  then ≤4 regime index CSVs (fetch_indices, MC 11545 — a missing index file
      │  is the tested CHOP×0.5 fallback, never a silent death)
      ├─ scores all 34 registered indicators per ticker through the 34-indicator
      │  island (`score_all` Pool(8) → `nightly_score.score_row` →
      │  `compute_rocket_score`; confidence/risk/regime applied). TWO-pass (D2):
      │  pass 1 all tickers social=False, pass 2 the preliminary top-500 social=True
      │   (adapter 2 s/call). KNOWN LIMIT: no sentiment service on this host, so
      │  social_score stays 0.0 for all until one ships — the gate ships, not the data.
      ├─ redraws the Indikatorer stats WEEKLY (Saturday): deterministic stratified
      │  sample of up to 500 tickers via sample_backtest (Pool(12)); other days the
      │  committed indicator_stats.json is carried with an explicit cadence note
      │  (not a failure)
      └─ fail-closed guard: renders to string first (every dynamic string HTML-escaped);
         unless the page passes ALL checks (registry floors + scored floor
         max(2 500, ceil(0.40 × registry)) + per-region coverage ≥ ceil(0.5 × members)
         + closed partition + markers for every tab + page ceiling + settled bars) it
         exits 1 WITHOUT writing or committing — the last good page stays live everywhere
      ▼
commits index.html + indicator_stats.json (exactly these two paths, nothing else)
      ▼
pushes to bryn1 main (the generator does the git plumbing itself)
```

### Publishing coupling (documented here since MC 11200 — it can silently freeze the product)

The publish step refuses to run while the **deploy tree** carries foreign commits ahead
of `bryn1/main` (journal: `FAIL-CLOSED — lokala commits före bryn1/main …`). Checking out
a card branch in the deploy checkout therefore stops nightly publishes until the branch
is landed/merged and the tree sits at the `bryn1/main` tip again — every stalled night is
a stale public page.

### Carry-forward rule

If the weekly backtest fails on a Saturday (any exception, or an unknown schema version in the
artifact), the generator keeps the **last committed** `indicator_stats.json` unchanged and embeds
that in the page; the footer shows the embedded `generated_at`, so stale evidence is visible, not
hidden. Monday–Friday the carry is SCHEDULED, not a failure — the page then says the stats update
weekly instead of claiming a failed run. A scoring failure never blocks the backtest and vice
versa.

### Logs & status

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)   # needed for --user queries over ssh
systemctl --user list-timers rocket-demo-publish.timer
journalctl --user -u rocket-demo-publish.service -n 100
```

### Rollback

1. Stop the pipeline: `systemctl --user disable --now rocket-demo-publish.timer`
2. Revert the unit changes: `git revert <commit>` for the commit touching
   `source/systemd/rocket-demo-publish.service` (and this README), then copy the reverted
   files back to `~/.config/systemd/user/` and `systemctl --user daemon-reload`.

The committed `index.html` / `indicator_stats.json` from the last successful nightly stay
served either way — the pipeline only ever adds publishes; reverting the units stops
future ones.
