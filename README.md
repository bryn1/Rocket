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

## The hosted page is a read-only demo snapshot

The page at **https://sibbamala.com/rocket/** is a **static, read-only snapshot** — it is
not a live scan and does not cover a global universe. Concretely, the snapshot contains:

- **35 tickers** (a fixed curated watchlist of large caps: AAPL, MSFT, SAP.DE, SAAB-B.ST, …)
- **3 regions**: Germany, Sweden, USA
- **~1 year of daily OHLCV** per ticker, fetched fresh on each run
- **Regenerated nightly** by the systemd user timer `rocket-demo-publish.timer` (05:00 UTC) —
  the page carries a freshness line stating when its data was fetched ("data hämtad …"), so
  how current the snapshot is is always visible on the page

It is a proof-of-concept of the pipeline end to end (real prices, real scoring math, a clean
read-only UI), sized as a small sample. The detail tab surfaces a representative signal per
category rather than all 34 indicators; the Indikatorer tab shows per-indicator backtest
evidence (signal counts, hit-rate and net % per horizon) recomputed nightly. It deliberately
makes **no claim** of live data, global coverage, or a full-universe scan.

## Nightly pipeline

- The systemd user timer `rocket-demo-publish.timer` (unit files in `source/systemd/`) fires at
  **05:00 UTC** and runs `source/scripts/generate_demo_page.py`: fetch → score → per-indicator
  backtest → render, fail-closed — nothing is written unless the rendered page passes the guard.
- The generator is the only writer of `index.html` and `indicator_stats.json`; it stages and
  commits exactly those two paths, and the push to `main` is what deploys the page.
- If the backtest step fails, the last committed `indicator_stats.json` is carried forward and
  the page shows its `generated_at` — stale evidence stays visible instead of disappearing.

## How to run the per-indicator backtest

From the repo root, against the cached CSVs (no network, no DB):

```
PYTHONPATH=source python3 -m rocket.backtest.indicator_eval \
    --cache-dir source/data/raw --out indicator_stats.json
```

`--tickers MSFT,SAAB_B` (CSV stems) narrows the run; the output is the same schema-v1 JSON
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
  - `source/app.py`, `source/server.py` — the Dash dashboard (engine UI; `server.py` is the hosting entrypoint)
  - `source/rocket/scoring/` — the Rocket score (`rocket_score.py`, weighting, risk, confidence)
  - `source/rocket/technical/` — the 34 indicator implementations
  - `source/rocket/dataquality/` — split-adjust, outlier detection, cleaning pipeline, position sizing
  - `source/rocket/data/`, `source/data_fetcher/` — data fetch and storage
  - `source/rocket/telegram_bot/`, `source/rocket/backtest/`, `source/rocket/scan_engine/` — bot, backtests, scans
  - `source/rocket/scoring/stocktwits.py` — StockTwits social-sentiment fetcher
  - `source/tests/`, `source/requirements.txt` — tests, pins
- `docs/ARCHITECTURE.md` — architecture of the shipped product (modules, pipeline, hosting contract)
