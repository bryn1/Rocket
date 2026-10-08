# Rocket — Stock Scanner

Rocket is a technical stock scanner: a data pipeline (yfinance / CoinGecko / OpenAvanza),
an evidence-weighted **Rocket score**, a Dash dashboard, a CLI, and a Telegram bot. This
repository holds the scanner engine and a hosted, read-only demo of its output.

## The Rocket score

For each ticker the engine runs **34 technical indicators** across four families and folds
them into a single evidence-weighted score:

| Family | Indicators | Examples |
|--------|-----------:|----------|
| Momentum   | 8  | RSI, MACD, ROC, Stochastic, Williams %R, CCI, Fear & Greed, Chimera |
| Trend      | 20 | EMA crossover, ADX, EMA 9/21/50/200, Ichimoku, Supertrend, AutoTrend, RubeGoldberg, Parabolic SAR, chart patterns, Echo Chamber, Match Finder, uFVG |
| Volatility | 3  | Bollinger Bands, ATR, Donchian Channel |
| Volume     | 3  | OBV, MFI, VWAP |

Of the **34 registered** indicators, **31 vote** on direction; **3 are risk-only**
(Bollinger Bands, ATR, Donchian Channel — they feed the risk multiplier, not the vote).
The indicators vote by family; a confidence and a risk multiplier are applied, and the
result is an **Overall score (0–100)** plus a **BUY / HOLD / SELL** signal per ticker, with
per-category sub-scores for Momentum, Trend, Volatility and Volume. The full list lives in
`source/rocket/scoring/rocket_score.py` (`INDICATORS`).

## The hosted page is a daily snapshot of the full universe

The nightly pipeline in this tree fetches, scores and publishes to
**https://sibbamala.com/rocket/** a **static, read-only snapshot** — not a live scan —
covering the **whole tracked universe**: every ticker in the registry
`source/rocket/data/universe_cache.json` — **12,793 unique tickers across 15 regions**
(usa 6,652, india 1,798, hongkong 1,122, japan 997, sweden 742, uk 356, norway 293, finland
194, australia 151, denmark 145, canada 144, korea 100, switzerland 35, germany 32, france 32;
registry timestamped 2026-09-13) — fetched and scored fresh on every nightly run. Concretely:

- **Every scored ticker** appears in its region tab; the **Alla** panel shows the **Top-500 by
  score**, and the filter box searches **every** panel, so any **scored** ticker is findable
  (rows exist only for scored tickers — the guard floor below guarantees ≥5,117 of them;
  a hit reveals the row and names its region)
- **~1 year of daily OHLCV** per ticker, delta-appended nightly into a local CSV store; only
  *settled* bars are stored (an in-progress session bar is stripped at write time)
- **Regenerated nightly** by the systemd user timer `rocket-demo-publish.timer` (05:00 UTC). The
  page carries a freshness line ("data hämtad …") *and* a coverage line
  ("**scorerade N av M tickers i registret**"), so both currency and coverage are always visible
- **A revert to a small page is guarded fail-closed**: the registry must stay ≥10,000 unique
  with ≥8 non-empty regions, committed-clean and within 0.8× of the last published size; the
  scored-rows floor is `max(2,500, 40 % of the registry)` = 5,117 at today's registry; every
  region tab must score ≥50 % of its registry members; the page has a 2.5 MB ceiling. A silent
  shrink is visible on the page as `scorerade 35 av 12793` — and cannot pass the guard.

It remains honest about what it is: **read-only** (no live queries, no autotrading), a
**snapshot** regenerated once a day, and each region row carries the signal plus four
per-category sub-scores (Momentum, Trend, Volatility, Volume) rather than all 34
indicators. The Indikatorer tab shows per-indicator backtest
evidence (signal counts, hit-rate and net % per horizon) from a deterministic **500-ticker
stratified sample**, redrawn **weekly (Saturday)** and carried with an explicit cadence note
on other days. It makes **no claim** of live data. The full-universe scope is the owner's
restored product (ruling 2026-10-07, MC 10220): the September universe coverage on today's
layout, updated daily.

> **Cutover status (2026-10-08 — delete at cutover close-out):** everything above describes the
> product on this branch, status **TESTED** (same scoper as `docs/ARCHITECTURE.md` "Läsregel");
> the URL above still serves the pre-cutover 35-ticker page until the first full-scale nightly
> lands (**MC 10227**, pending).

## Nightly pipeline

- The systemd user timer `rocket-demo-publish.timer` (unit files in `source/systemd/`) fires at
  **05:00 UTC** and runs `source/scripts/generate_demo_page.py` through the stages in
  `docs/ARCHITECTURE.md` §2: registry load (pre-network floors) → batched delta/backfill fetch
  → parallel scoring of every stored ticker → weekly Indikatorer sample backtest (Saturday) or
  honest carry → render → fail-closed guard → publish. Nothing is written unless the rendered
  page passes every guard; on failure the last good page stays live.
- The generator is the only writer of `index.html` and `indicator_stats.json`; it stages and
  commits exactly those two paths, and the push to `main` is what deploys the page.
- The registry itself is **never rebuilt nightly**. The only sanctioned path to a new
  `universe_cache.json` is the guarded CLI `python3 source/scripts/refresh_universe.py`
  (bounded shrink/growth, per-region and superset rules — refuse leaves the file untouched).
- If the weekly backtest fails, the last committed `indicator_stats.json` is carried forward and
  the page shows its `generated_at` — stale evidence stays visible instead of disappearing.

## How to run the per-indicator backtest

From the repo root, against the cached CSVs (no network, no DB):

```
PYTHONPATH=source python3 -m rocket.backtest.indicator_eval \
    --cache-dir source/data/raw --out indicator_stats.json
```

`--tickers MSFT,SAAB_B_ST` (CSV stems) narrows the run; the output is the same schema-v1 JSON
artifact the page's Indikatorer tab consumes. Architecture, seams and the hosting contract:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Repository layout

The repo root holds **the served content** (the demo page, its backtest evidence, and the
served legacy pages); the entire Python engine lives under `source/` (versioned but **not**
served — see `hosting.yaml`), so the static hosting alias can never expose engine source:

- Served pages (all live at https://sibbamala.com/rocket/): `index.html` (the read-only demo),
  `indicators.html` (methodology), `portfolio.html`, `region-top25.html`, `top25.json` (the
  last three are frozen legacy snapshots kept live — see `docs/ARCHITECTURE.md` §8)
- `indicator_stats.json` — per-indicator backtest evidence (written + committed by the
  nightly generator)
- `hosting.yaml` — deployment manifest (static demo; `root: apps/rocket`)
- `source/` — the full scan engine (not served):
  - `source/app.py`, `source/server.py` — the Dash dashboard (engine UI; not served — the hosted page is static)
  - `source/rocket/scoring/` — the Rocket score (`rocket_score.py`, weighting, risk, confidence)
  - `source/rocket/technical/` — the 34 indicator implementations
  - `source/rocket/dataquality/` — split-adjust, outlier detection, cleaning pipeline, position sizing
  - `source/rocket/data/`, `source/data_fetcher/` — data fetch and storage, incl. the tracked
    registry `universe_cache.json` the nightly loads (never rebuilt nightly; see
    `source/scripts/refresh_universe.py`)
  - `source/rocket/telegram_bot/`, `source/rocket/backtest/`, `source/rocket/scan_engine/` — bot, backtests, scans
  - `source/rocket/scoring/stocktwits.py` — StockTwits social-sentiment fetcher
  - `source/tests/`, `source/requirements.txt` — tests, pins
- `docs/ARCHITECTURE.md` — architecture of the shipped product (modules, pipeline, hosting contract)
