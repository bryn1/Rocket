# T23 hygiene close-out report (MC 10313) — one row per numbered item, 2026-10-19

1. DEAD LEGACY — EXECUTED. Census (source/build-10313/census.txt, BFS over imports from the
   18 live entry files = app.py, server.py, scripts/*.py): daily_scoring.py, daily_scan_cron.py,
   generate_daily_signals.py, nightly_scan.py, portfolio_check_cron.py are ORPHAN-MODULES (no live
   code reaches them; their two systemd units are NOT enabled on the host — only
   rocket-demo-publish.timer is loaded). scripts/fetch_history.py: no importer, no ExecStart, the
   10-y parquet era it bootstraps is owner-bortvald (ARCHITECTURE §1). DELETED: 6 py + 4 unit
   files (daily-scan.*, portfolio-check.*). Suite after deletion: 416 passed, 3 skipped, rc=0.
   Docs grep: every named file gone from docs/ARCHITECTURE.md, README.md, source/systemd/README.md.
2. app.py 849 ln > 600 — RULED LEAVE WITH REASON, reason line now IN THE HEADER (after the module
   docstring): debt predates MC 10220 (814 ln at f2c02f6); one file keeps the Dash callback
   registration single-source. Ceiling satisfied via the header reason, no split churn.
3. hosting.yaml _comment — EXECUTED. The duplicate live-URL sentence ("All five verified HTTP 200
   at https://sibbamala.com/rocket/ on 2026-10-01.") deleted; README cutover blockquote CHECKED
   with date: close-out 2026-10-19 the live page serves the FULL universe (12 122 members,
   10 832 scored, bar stamp 2026-10-17; MC 10227 verified) — the stale pending-note blockquote is
   replaced by the dated close-out check line.
4. fetch_store §5 3b split-requeue pacing — ALREADY PACED, DOCUMENTED. T20 (MC 10311, commit
   6a75c6e) put the end-of-stage re-queue round on the SAME pace() helper and ladder state as the
   main walk (module docstring lines 24–34: one walk conceptually; <=512 x 10 s cap < 10800 s
   TimeoutStartSec). No further change needed.
5. P4 delta window — EXECUTED. Delta batches now fetch start= the OLDEST member's last bar
   (fetch_store.delta_start + _fetch_batch start param; backfill keeps period=1y, start None).
   New test test_delta_batches_start_at_oldest_member_last_bar is RED under the pre-fix code
   (old call passed only (batch, period) -> start stayed None). Transcript: suite-final.txt.
6. golden-35 next era — EXECUTED (mechanism note). tests/test_golden35_fixtures.py docstring now
   states the next-era pin recipe: copy the era's store CSVs into tests/data/<era>/, one SHA256SUMS
   byte-lock, one scoring-pair test per pinned digit; owner opts in per era.
