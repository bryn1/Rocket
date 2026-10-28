# Rocket — Stock Scanner

Rocket is a technical stock scanner: a nightly yfinance OHLCV pipeline into a
local CSV store, an evidence-weighted **Rocket score**, and a hosted, read-only
demo page of its output. This repository holds the scanner engine and the
hosted demo. The retired chat-bot-era product (messaging bot, crypto price
sources, broker price sources, portfolio scans) is **not** the product — its
dead packages sat in the tree until the retirement batches (MC 11199, B1–B6)
deleted them 2026-10-24; `docs/ARCHITECTURE.md` §9 keeps the historical record.

## The Rocket score

For each ticker the engine runs **34 technical indicators** across four families:

| Family | Indicators | Examples |
|--------|-----------:|----------|
| Momentum   | 8  | RSI, MACD, ROC, Stochastic, Williams %R, CCI, Fear & Greed, Chimera |
| Trend      | 20 | EMA crossover, ADX, EMA 9/21/50/200, Ichimoku, Supertrend, AutoTrend, RubeGoldberg, Parabolic SAR, chart patterns, Echo Chamber, Match Finder, uFVG |
| Volatility | 3  | Bollinger Bands, ATR, Donchian Channel |
| Volume     | 3  | OBV, MFI, VWAP |

The **score on the hosted page** is the nightly path
(`source/scripts/score_all.py` → the `nightly_score.py` adapter →
`compute_rocket_score` in `source/rocket/scoring/rocket_score.py` — owner
ruling "B", MC 11474; live from the 2026-10-28 nightly, and the page header
says it: "Rocket-score v2"): the 31 voting indicators drive family consensus,
and **confidence, risk and regime multipliers are applied to the page
score**. Stated honestly: the three volatility-family indicators (Bollinger
Bands, ATR, Donchian Channel) are **risk-only** — they never vote, they feed
the risk multiplier, and the Volatility sub-score *is* that risk score
(0–100), not a weight bucket; the row's **BUY / HOLD / SELL** comes from the
final score's strength thresholds, not a raw per-indicator majority. The
Overall (0–100) is the island's final score mapped through the weighter's
0–100 shape (`scoring/weighter.py`, reused). The old `app.py` seam chain is
retired — `source/app.py` is now a thin delegate into the same 34-indicator
registry, so the unserved Dash UI cannot carry a disagreeing indicator set.
The full indicator list lives in `rocket_score.py` (`INDICATORS`).

## The hosted page is a daily snapshot of the full universe

The nightly pipeline in this tree fetches, scores and publishes to
**https://sibbamala.com/rocket/** a **static, read-only snapshot** — not a live
scan — covering the **whole tracked universe**: every ticker in the registry
`source/rocket/data/universe_cache.json`, fetched and scored fresh on every
nightly run. Registry size, per-region counts and timestamp are volatile (they
move only through the guarded refresh CLI below), so this contract carries no
numbers: **the current registry epoch is recorded, dated, in
[`LEDGER.md`](LEDGER.md)**. Concretely:

- **Every scored ticker** appears in its region tab; the **Alla** panel shows
  the **Top-500 by score**, and the filter box searches **every** panel, so any
  **scored** ticker is findable (rows exist only for scored tickers — the guard
  floor below guarantees a minimum count of them; a hit reveals the row and
  names its region)
- **~1 year of daily OHLCV** per ticker, delta-appended nightly into a local
  CSV store; only *settled* bars are stored (an in-progress session bar is
  stripped at write time); every CSV is written atomically (per-writer temp +
  rename) and a concurrent second entry into the pipeline is refused loudly by
  a store lock
- **Regenerated nightly** by the systemd user timer `rocket-demo-publish.timer`
  (05:00 UTC). The page carries a freshness line ("data hämtad …") *and* a
  coverage line ("**scorerade N av M tickers i registret**"), so both currency
  and coverage are always visible from the artifact itself
- **A revert to a small page is guarded fail-closed**: every registry region
  key and ticker entry must pass the loader's character-allowlist gate (a bad
  registry REFUSES the run, nothing is silently dropped); the registry must
  stay ≥ 10,000 unique with ≥ 8 non-empty regions, committed-clean and within
  0.8× of the last published size; the scored-rows floor is
  `max(2 500, ceil(0.40 × registry size))` — exact integer **ceil**, so a
  boundary region can never round its way past a floor; every region tab must
  score ≥ `ceil(50 %)` of its registry members; the page has a 2.5 MB ceiling.
  A silent shrink cannot pass the guard — it would show on the page as
  `scorerade 35 av M` and refuse the publish.

It remains honest about what it is: **read-only** (no live queries, no
autotrading), a **snapshot** regenerated once a day, and each region row
carries the signal plus four per-category sub-scores rather than all 34
indicators. The Indikatorer tab shows per-indicator backtest evidence (signal
counts, hit-rate and net % per horizon) from a deterministic **stratified
sample of up to 500 tickers** (the committed artifact's own `sample.tickers`
count is the authoritative post-filter number), redrawn **weekly (Saturday)**
and carried with an explicit cadence note on other days. It makes **no claim**
of live data. The full-universe scope is the owner's restored product (ruling
2026-10-07, MC 10220): the September universe coverage on today's layout,
updated daily.

## Nightly pipeline

- The systemd user timer `rocket-demo-publish.timer` (unit files in
  `source/systemd/`) fires at **05:00 UTC** and runs
  `source/scripts/generate_demo_page.py` through the stages in
  `docs/ARCHITECTURE.md` §2: registry load (charset gate + pre-network floors)
  → batched delta/backfill fetch → parallel scoring of every stored ticker →
  weekly Indikatorer sample backtest (Saturday) or honest carry → render (every
  dynamic string HTML-escaped) → fail-closed guard → publish. Nothing is
  written unless the rendered page passes every guard; on failure the last good
  page stays live. A non-blocking flock in the store root refuses a second
  concurrent entry LOUDLY with zero writes, and `--dry-run` is READ-ONLY
  against the store (facts ride, CSVs never).
- The generator is the only writer of `index.html` and `indicator_stats.json`;
  it stages and commits exactly those two paths, and the push to `main` is what
  deploys the page. **Known coupling (undocumented until MC 11200):** the
  publish guard refuses while the deploy tree carries foreign commits ahead of
  `bryn1/main` — journal line `FAIL-CLOSED — lokala commits före bryn1/main` —
  so checking out a card branch in the deploy tree freezes publishing until it
  is landed or checked out again.
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

The repo root holds **the served content** (the demo page and the served legacy
pages) plus the committed backtest evidence; the entire Python engine lives under
`source/` (versioned but **not** served — see `hosting.yaml`), so the static
hosting alias can never expose engine source:

- Served pages (all live at https://sibbamala.com/rocket/): `index.html` (the read-only demo),
  `indicators.html` (methodology), `portfolio.html`, `region-top25.html`, `top25.json` (the
  last three are frozen legacy snapshots kept live — see `docs/ARCHITECTURE.md` §8;
  `region-top25.html`'s Score column was REGENERATED 2026-10-24 from the same-epoch
  published `index.html` — owner ruling "1c", MC 11188 — by
  `source/scripts/regen_top25_scores.py`; tickers absent from the scored set render –,
  never a fake 0.00)
- `indicator_stats.json` — per-indicator backtest evidence, written and committed by the
  nightly generator but **not served** (the page embeds it; the hosting contract serves
  exactly the five pages above)
- `hosting.yaml` — deployment manifest (static demo; `root: apps/rocket`)
- `source/` — the engine (not served):
  - `source/scripts/` — the nightly pipeline: `generate_demo_page.py` (orchestrator + entry
    lock), `full_universe.py` (registry loader, charset gate, floors, the ONE constants
    home), `fetch_store.py` + `store_io.py` (the sole CSV-store writer),
    `fetch_indices.py` (the ≤4 regime index CSVs, nightly), `score_all.py` (two-pass
    Pool(8) driver), `nightly_score.py` (the adapter — one mapping authority + explicit
    D2/D3 kwargs policy), `sample_backtest.py` + `demo_indicators.py` (weekly Indikatorer branch), `demo_render.py`
    (the single HTML-escape point), `demo_publish.py` (publish mechanics), `excluded_tickers.py`
    (delisting exclusion list), `refresh_universe.py` (the ONLY
    registry writer, guarded), `registry_purge.py`
  - `source/app.py` — a local Dash UI (engine UI, not served) as **thin
    delegates** into the 34-indicator island (MC 11474 B2): the nightly never
    enters this file — scoring authority is `nightly_score.score_row`;
    `source/server.py` is a legacy local-render entry, likewise not served
  - `source/rocket/scoring/` — the Rocket score (`rocket_score.py`, weighting, risk, confidence)
  - `source/rocket/social/` — **GATED** scoring input (D2, MC 11474): pass 1
    scores every ticker with social OFF; the nightly re-scores exactly the
    preliminary **top-500** with social ON, bounded by the adapter's 2 s
    per-call timeout (`nightly_score.py` — the single choke point; every call
    site passes the flag explicitly, never a hidden default).
    **KNOWN LIMIT: this host runs no sentiment service, so `social_score`
    stays 0.0 for everyone until one ships — the wiring ships the gate, not
    the data.**
  - `source/rocket/technical/` — the 34 indicator implementations
  - `source/rocket/data/` — the tracked registry `universe_cache.json` (never rebuilt nightly;
    see `refresh_universe.py`), batched fetching, the class-share request form
  - `source/rocket/backtest/` — no-look-ahead indicator evaluation (`indicator_eval.py`)
    and the walk-forward campaign engines
  - `source/rocket/dataquality/` — split-adjust (used nightly via `store_io`) and outlier
    detection; the rest of the package is retired-era
  - the retired-era dead mass is **fully deleted** (2026-10-24, MC 11199 batches
    B1–B6): the chat-bot-era messaging, scan and user-store packages, the era
    scripts, the daily-cron chain, the dead scoring slivers, the retired fetch
    package, the util package, the dead backtest modules and the remaining strays —
    65 files; the inventory, companions and grep-zero proof live in
    `.audits/20261023-0705-full-reaudit/`
  - `source/tests/`, `source/requirements.txt` — tests, pins
- `docs/ARCHITECTURE.md` — architecture of the shipped product (modules, pipeline, hosting contract)
