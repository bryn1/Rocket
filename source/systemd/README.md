# Rocket Stock Scanner - Systemd Services

## Setup

Copy the service and timer files to the systemd user directory:

```bash
cp daily-scan.service ~/.config/systemd/user/
cp daily-scan.timer ~/.config/systemd/user/
cp portfolio-check.service ~/.config/systemd/user/
cp portfolio-check.timer ~/.config/systemd/user/
```

## Enable

```bash
systemctl --user enable daily-scan.timer
systemctl --user enable portfolio-check.timer
systemctl --user start daily-scan.timer
systemctl --user start portfolio-check.timer
```

## Schedule

- **daily-scan**: Runs at 22:00 every night
- **portfolio-check**: Runs every 5 minutes

## Logs

```bash
journalctl --user -u daily-scan.service
journalctl --user -u portfolio-check.service
systemctl --user list-timers daily-scan.timer portfolio-check.timer
```

## Manual Run

```bash
systemctl --user start daily-scan.service
systemctl --user start portfolio-check.service
```

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
rocket-demo-publish.service (Type=oneshot, TimeoutStartSec=1800 — the in-process
      │                      backtest takes ~5-10 min, default ~90 s would kill it)
      ▼
source/scripts/generate_demo_page.py
      ├─ fetches OHLCV for the 35-ticker universe (usa/sweden/germany)
      ├─ scores all 34 registered indicators per ticker
      ├─ runs the per-indicator backtest IN-PROCESS (rocket.backtest.indicator_eval)
      └─ fail-closed guard: renders to string first; unless the page passes the
         checks (enough scored rows + all tabs + freshness markers) it exits 1
         WITHOUT writing or committing — the last good page stays live everywhere
      ▼
commits index.html + indicator_stats.json (exactly these two paths, nothing else)
      ▼
pushes to bryn1 main (the generator does the git plumbing itself)
```

### Carry-forward rule

If the backtest fails (any exception, or an unknown schema version in the artifact), the
generator keeps the **last committed** `indicator_stats.json` unchanged and embeds that in
the page; the footer shows the embedded `generated_at`, so stale evidence is visible, not
hidden. A scoring failure never blocks the backtest and vice versa.

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
