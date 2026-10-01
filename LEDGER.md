# Rocket — LEDGER

Project ledger (one per project). Status lines are appended by the closing commit of each card.

## MC 3841 — Port TheUltimator5 indicator suite (4 work items)

- 2026-09-26 MC 3841.T1 — full-logic RubeGoldberg top/bottom finder ported to `source/rocket/technical/rube_goldberg.py` (leading trigger + lagging SAR/RSI-ROC confirmation, multi-symbol DI divergence), 12 tests. Commit 0e74605. TESTED.
- 2026-09-26 MC 3841.T2 — Echo Chamber + Match Finder ported to `source/rocket/technical/pattern_match.py` (sliding-window Pearson search, time-dilation sweep, forward projection, reference-series matcher), 10 tests. Commit 2723078. TESTED.
- 2026-09-26 MC 3841.T3 — Fear & Greed + Chimera composites ported to `source/rocket/technical/composites.py` (7-component CNN-style index; 5-component z-normalized oscillator with SMA cross trend state), 14 tests. Commit dc5543d. TESTED.
- 2026-09-26 MC 3841.T4 — unFair Value Gap Detector ported to `source/rocket/technical/ufvg.py` (low-ADX DI-spike gap fills, gap-line tracking, consolidation state; HTF panels documented as chart-only deviation), 8 tests. Commit 961d3b4. TESTED.
- 2026-09-26 MC 3841 integration — all five new indicators registered in `source/rocket/technical/__init__.py`, `families.py`, `rocket_score.py` (INDICATORS 29→34, DIRECTION 26→31); README indicator count claim updated; full suite 244 passed / 1 skipped. TESTED.

STATUS: OPEN — pending MC done-gate verification.
- 2026-09-30 MC 3848 — per-region demo page + nightly publish pipeline: `source/scripts/generate_demo_page.py` (fetch → score via app.py's own scoring path → render index.html with one tab per region + ticker filter → pull --rebase/commit/push to bryn1 main), germany→eu fix in app.py `_REGION_MAP`, systemd `rocket-demo-publish.timer` (05:00 UTC nightly, VM350, enabled + verified). Fresh page published to bryn1 main (5da15f3, data 2026-09-30 19:16 UTC, 35/35 tickers scored). NOTE: bryn1 main was externally force-reset e6061f0→89377c2 during the session; restored by fast-forward re-push — actor unidentified, owner informed. TESTED.

## MC 3874 — Honest per-indicator backtests + daily-fresh product-grade page

- 2026-10-01 MC 3874 — `source/rocket/backtest/indicator_eval.py` (249 ln, 10 tests): no-look-ahead evaluation of all 34 registered indicators on the production scoring path (edge-triggered, next-bar-open entry, horizons 5/10/20 d, 0.3 pp roundtrip cost, deterministic) → `indicator_stats.json`; generator v2 fail-closed (MIN_ROWS 25 + marker gates, `id="freshness"` weekday-aware banner, Indikatorer tab, cache `date` dtype fix in `generate_demo_page.py` `_write_cache`/`_read_cache` + back-compat reader, 375px overflow fix), unit `TimeoutStartSec=1800` + installed-and-rearmed timer, ONE nightly green through the installed unit observed (8m33s, publish a0acc58 → bryn1 main, live page fresh). Full suite 267 passed / 1 skipped. Cycle-2 verdicts on this tree: TEST PASS, DA SHIP, ARCH PASS (`.audits/202610011531-903ee481/`). Known limitations: signals.db engine path untouched (legacy), metrics.py pairing unused by this runner (runner carries its own pairing), app.py Dash UI still 28/34 until its converter lands. VERIFIED.
