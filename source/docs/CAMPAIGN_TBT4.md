# CAMPAIGN_TBT4 — full-universe backtest campaign plan (MC 10373, T-BT4)

Goal: one honest, hermetic, deterministic campaign over the WHOLE local store
(`source/data/raw`, backfill-complete, registry-truth purged per MC 10738),
with walk-forward validation, a single never-touched holdout, and multiple-
testing correction. Engine: `rocket/backtest/full_campaign.py`, built ONLY on
the shipped sample-backtest seam (`scripts/sample_backtest.py` +
`rocket/backtest/indicator_eval.py`); no new indicator math.

## 1. Indicator inventory (from source, 34 classes)

Registry: `rocket.scoring.rocket_score.INDICATORS`, in declaration order:
RSI, MACD, ROC, Stochastic, WilliamsR, CCI, EMACrossover, ADX, EMA9, EMA21,
EMA50, EMA200, IchimokuCloud, Supertrend, AutoTrend, RubeGoldberg,
ParabolicSAR, DoubleTopBottom, HeadShoulders, WedgePattern, AutoFractal,
CupAndHandle, PatternDetectorCombined, EchoChamber, MatchFinder, FearGreed,
Chimera, UnFairValueGapDetector, BollingerBands, ATR, DonchianChannel, OBV,
MFI, VWAPIndicator.

A "combination" = (indicator × side × primary horizon). The seam's law is
fixed: edge-triggered signal, entry at next bar OPEN, exit at the first
opposite-signal bar at or after entry else at horizon end; horizons (5, 10,
20); horizon 10 is PRIMARY for ranking, 5/20 are descriptive.

## 2. Data and universe

- Store: `source/data/raw/TICKER_REGION.csv`, 10,875 files, ~250 daily bars
  each (269-date global calendar, 2025-10-07 .. 2026-10-16).
- Read via `store_io.read_store` (the seam's only reader). Frames are
  Title-cased once (`to_indicator_frame`) and shared by the replay.
- Universe = every ticker in the store = TODAY's registry truth.

## 3. Methodology

- Warm-up law: the seam never emits before bar 60 (`WARMUP_BARS`), so the
  first 60 calendar dates of the global union are consumed by warm-up and are
  not fold days. Evaluated calendar = 209 dates.
- Walk-forward: the evaluated calendar is cut into 2 contiguous equal-count
  FOLDS + 1 final HOLDOUT window. Each fold replays every indicator over a
  slice with a LEFT margin of exactly `WARMUP_BARS` bars (so fold-window days
  all emit) and a RIGHT edge at the window end; the last ≤20 signals of each
  window lose their exit and land in the seam's `incomplete` counts — one
  named cost, symmetric across indicators.
- Holdout: the LAST window, never touched during selection. After ranking,
  the top-N (N=8) leaderboard names are re-validated on it exactly once.
- Multiple testing: per (indicator, fold) one-sided t from the seam's
  `evidence_t` (primary-horizon LONG), p by the normal approximation (the
  seam's own n ≥ 20 floor keeps it exact to 3 decimals); Benjamini-Hochberg
  with q = 0.10 over the joint 34×2 family. A name is `bh_pass` iff its fold
  tests sit at or below the BH cut.
- Metrics per combination: n, hit_rate, avg/median net %, best/worst — all
  post-friction — plus a n-weighted avg net % across folds with a t-based 95%
  CI from n and the pooled per-fold sd.
- Friction assumptions: 0.1% commission + 0.05% slippage per leg
  (ROUNDTRIP_PCT = 0.3% per round trip), close-signal → next-open entry, no
  market-impact term.

## 4. Artifacts

- `docs/CAMPAIGN_TBT4.md` — this plan.
- `rocket/backtest/full_campaign.py` — hermetic engine (fork Pool over
  indicator chunks, the sample seam's EVAL_WORKERS idiom).
- `/tmp/campaign_tbt4.json` — schema-v1 leaderboard + holdout doc (regener-
  ated deterministically; copy pinned in `.audits/<run>/RESULT.md`).
- `tests/test_full_campaign.py` (+ `tests/full_campaign_fixtures.py`) — 9
  green tests incl. planted-bad reds on the BH ordering.

## 5. Honest limitations

- Survivorship: the universe is TODAY's registry; delisted names are absent,
  so hit rates carry a mild survivorship lift.
- Look-ahead residual: warm-up uses only prior bars, but the seam's own
  laws remain (next-open entry; exit-on-opposite), and no point-in-time
  fundamentals exist in the store.
- Registry churn: the T35 purge (1,871 removed members) means early-fold and
  late-fold universes differ slightly per ticker availability.
- Regime dependence: ~250 bars per ticker (one 2025-10..2026-10 window);
  fold results are one market regime's snapshot; BH q=0.10 bounds, not
  eliminates, false discovery.
- Product-surface claims (page numbers shown in the app) are DEFERRED to the
  owner ruling + external-exposure-gate; this campaign feeds the numbers.
