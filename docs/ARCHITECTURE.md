# 🚀 Rocket — Arkitektur

> Denna fil beskriver den **levererade produkten**: en read-only demosida som regenereras
> varje natt. Den tidigare versionen (skapad 2026-07-24, låg som `ARCHITECTURE.md` i `source/`)
> beskrev en pensionerad vision — 25k tickers, Telegram-dagsrekommendationer, 10-årig
> historik — som ägaren valde bort; den flyttades och skrevs om 2026-10-01 (MC 3874)
> via `git mv` hit till `docs/ARCHITECTURE.md` (layout v2: ett arkitekturdokument
> på rotens `docs/`).

**Läsregel:** allt märkt **"ny — MC 3874"** är måltillståndet som landar i denna köra
(samma merge som koden); allt övrigt i dokumentet är sant mot huvudgrenen idag.

## 1. Vad produkten är

Read-only **decision-support**: en statisk demosida med Rocket-score för **35 kuraterade
tickers** i **3 regioner** (USA, Sverige, Tyskland), regenererad varje natt. Ingen
autotrading, ingen brokerkoppling, inga levande dataflöden mot läsaren — sidan är en
snapshot med öppen uppgift om hur färsk datan är. Värd:
**https://sibbamala.com/rocket/** (statisk nginx under alias `/rocket/`; rot `apps/rocket`
i hosting-monorepen, speglad från repo-root).

## 2. Dataflöde — den enda pipelinen

```
rocket-demo-publish.timer  (systemd user-unit, OnCalendar 05:00:00 UTC, källa source/systemd/)
 └─ rocket-demo-publish.service  (TimeoutStartSec=1800)
     └─ source/scripts/generate_demo_page.py
         1. fetch_ohlcv (yfinance, ~1 år dagdata) → cache source/data/raw/<ticker>.csv
         2. Rocket-score per ticker — Title-case-omvandling av frames ("ny — MC 3874")
            gör att alla 34 indikatorer faktiskt körs (för var 28/34, se §8.2)
         3. per-indicator-backtest in-process (rocket.backtest.indicator_eval, "ny — MC 3874")
         4. render index.html: regionflikar + Indikatorer-flik + färskhetsbanner
         5. fail-closed guard: ≥25/35 scoreade rader OCH alla markörer i HTML:en —
            annars skrivs INGET och den gamla sidan stannar live
         6. skriv index.html + indicator_stats.json → git add exakt dessa två → commit + push
```

Misslyckas backtest-steget (3): senast commitade `indicator_stats.json`
**carry-forwardas** orört in i sidan, så stale evidence syns (dess `generated_at`
visas) i stället för att försvinna. Guarden i steg 5 är fixen mot att tomma sidan
publicerats.

## 3. Moduler (under `source/`)

| Modul | Innehåll | Status |
|---|---|---|
| `rocket/technical/` | **34 indikatorer** + bas-kontraktet `BaseIndicator.calculate(df) → IndicatorResult` (scalärer för sista baren, aldrig per-bar-serier): `momentum.py`, `trend.py`, `volatility.py`, `volume.py`, `advanced.py`, `patterns.py`, `pattern_match.py`, `composites.py`, `ufvg.py`, `families.py`, `regime.py` | levererad |
| `rocket/scoring/` | `rocket_score.py`: `INDICATORS` = **34** registrerade, `DIRECTION_INDICATORS` = **31** röstande, **3 risk-only** (BollingerBands, ATR, DonchianChannel). Familjefördelning ur kod (`INDICATORS` + `_NAME_TO_FAMILY`): **8 momentum / 20 trend / 3 volatility / 3 volume = 34**. Tillhörande `confidence.py`, `risk.py`, `filter.py` | levererad |
| `rocket/backtest/` | legacy `engine.py` (DB-kopplad scan/backtest), `metrics.py` (se §8.1), `sensitivity.py`, `meme_backtest.py` — samt **`indicator_eval.py` — ny — MC 3874**: per-indicator-replay med expanderande fönster (`calculate(df.iloc[:t])` per bar — samma produktionsväg som sidan, ingen andraisimplementation), edge-triggerade entries, entry nästa bar-OPEN (ingen lookahead), horisonter **5/10/20 d** med **10 d som primär**, kostnader 0.1 % commission + 0.05 % slippage = **0.3 % roundtrip** (samma konstanter som `engine.py:303–305`), bevisvikt `strong/moderate/weak/none/insufficient/error` ur ensidigt t-test på primära horisontens lång sida, deterministisk (ingen RNG; samma frames ⇒ samma JSON förutsatt `generated_at`) | ny |
| `rocket/data/fetcher.py` | yfinance OHLCV; returnerar **lowercase** OHLCV-kolumner med `date` som index — indikatorerna kräver Title-case, omvandlas av `to_indicator_frame` (i `indicator_eval.py`, "ny — MC 3874") | levererad |
| `scripts/generate_demo_page.py` | orkestratorn (steg 1–6 ovan); skriven om i MC 3874 (guard, carry-forward, `--dry-run` renderar till `.tmp/` utan att röra root-artifakterna) | omarbetad MC 3874 |
| `scripts/demo_universe.py` — ny — MC 3874 | `UNIVERSE` (35 tickers), `REGION_LABELS`, `CACHE_DIR`, `cache_filename` | ny |
| `scripts/demo_render.py` — ny — MC 3874 | HTML-rendering: regionflikar, Indikatorer-flik, färskhetsbanner | ny |
| `app.py`, `server.py` | Dash-dashboard — engine-UI:t, **serveras inte** (allt under `source/` är versionerat men inte served) | levererad |

## 4. Entrypoints

| Kommando (från repo-root) | Vad |
|---|---|
| `python3 source/scripts/generate_demo_page.py` | sidgeneratorn — körs av timern; flaggorna `--dry-run`, `--skip-fetch` |
| `PYTHONPATH=source python3 -m rocket.backtest.indicator_eval --cache-dir source/data/raw --out indicator_stats.json` | per-indicator-backtest från cache-CSV:er (nätverksfritt, ingen DB); `--tickers stem,…` valfritt — **ny — MC 3874** |
| `python3 source/app.py` | Dash-UI lokalt (engine, inte served) |
| `engine.py`-CLI:n | legacy-scan/-backtest mot `signals.db` |

## 5. Schemaläggning

`rocket-demo-publish.timer` — **systemd user-unit**, `OnCalendar=*-*-* 05:00:00 UTC`,
unit-filerna versioneras i `source/systemd/`. Aktiverad: nattkörningen 2026-10-01
kl. 05:00 UTC syns på den serverade sidan som `data hämtad 2026-10-01 05:01 UTC`.

## 6. Data stores

- `source/data/raw/*.csv` — OHLCV-cache, **gitignored**, skrivs av generatorn.
- `source/data/signals.db` — legacy SQLite-databas från engine-eran, gitignored och
  **finns inte i checkouten idag**; används endast av `engine.py`:s egen CLI-väg
  (scan/backtest-skript). Nightly-pipelinen rör den aldrig.
- Committade **root-artifakter**: `index.html` (regenereras nattligen),
  `indicator_stats.json` (**ny — MC 3874**; skrivs och committas av publish-steget,
  `schema_version` 1, fältkontrakt i `indicator_eval.py`), `top25.json` (frozen snapshot).
- Legacy-sidor som **serveras men saknar generator i repo** (`indicators.html` =
  metodiksida, `portfolio.html`, `region-top25.html`): **behålls** — deras URL:er är
  live; se §8.3.

## 7. Hosting-kontrakt

- **Portar: inga.** Statisk hosting, alias `/rocket/` på https://sibbamala.com; ingen
  process körs för sidan — den är committade filer.
- Serverat innehåll = **fem serverade sidor**: `index.html` (via `/rocket/`),
  `indicators.html`, `portfolio.html`, `region-top25.html`, `top25.json` — alla
  verifierade HTTP 200 2026-10-01.
- `source/` är versionerat men **inte** serverat; ingen `*.py` är nåbar via den
  serverade roten (`hosting.yaml` + monorepareglan "Files under source/ are versioned
  but NOT served").

## 8. Kända begränsningar — EJ fixade i MC 3874

1. **`rocket/backtest/metrics.py`: trasig trade-parning.** `calculate_metrics` keyar
   BUY på inköpsdatum och slår upp SELL på säljdatum — paren matchar aldrig och
   win/loss-statistiken blir ≈ 0 för allt som inte är samma-dags-affärer.
   Disqualificerad för återanvändning (därför bygger Indikatorer-flikens statistik på
   egen event-study i `indicator_eval.py`). Separat fix-kort.
2. **Dash-sidan i `app.py` scorear 28/34.** Sex indikatorer (mönsterklasserna) kastar
   tyst `KeyError` på lowercase-frames, så Dash-UI:t och den idag commitade
   demosidan körs på 28 av 34 indikatorer. Samma `to_indicator_frame`-omvandlare
   ("ny — MC 3874") skall appliceras även på `app.py`-vägen — follow-up-kort. Den
   serverade sidans 34/34-score kommer med MC 3874:s omarbetade generator; första
   nattkörningen därefter syns en engångsförskjutning i poängen (6 mönster-röster
   tillkommer).
3. **Frozen snapshots:** `region-top25.html` + `top25.json` (samt `portfolio.html`)
   har ingen generator i repo och uppdateras inte — de serveras som orörda,
   committade snapshots. Ägarbeslut väntar: regenerera eller pensionera.

## 9. Pensionerad vision (historik)

Föregångardokumentet (2026-07-24) planerade 25 000+ tickers, 10 år historik, Telegram
daily recs och options-/social-data-skalning. Den linjen är bortvald av ägaren och
lever inte i produkten; moduler som `data/universe_builder.py`, `social/`, `quant/`
finns kvar i `source/` som engine-ärva men ingår inte i den serverade produkten.

---

*Uppdaterad: 2026-10-01 (MC 3874). Sanning prövas mot trädet: om en uppräkning i denna
fil motsägs av koden, är dokumentet fel — fixa dokumentet, inte sanningen.*
