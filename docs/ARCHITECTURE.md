# 🚀 Rocket — Arkitektur

> Denna fil beskriver den **levererade produkten**: en read-only demosida över **hela det
> trackeda universum**, regenererad varje natt. Föregångardokumentet (2026-07-24) beskrev en
> pensionerad vision (25k tickers, dagsrecs via chattbot, 10-årig historik) som då var bortvald;
> den äldre 35-ticker-demolinjen (MC 3874) är i sin tur **överkörd av ägarens beslut
> 2026-10-07 (MC 10220)**: september-universumet återställt på dagens layout, uppdaterat
> dagligen, med risken för återfall till 35 permanent hanterad. Resten av den gamla visionen
> (10-år historik, chattbot-recs, autotrading) är **fortfarande bortvald**. Dokumentet
> flyttades hit till `docs/ARCHITECTURE.md` 2026-10-01 (MC 3874, layout v2).

**Läsregel:** dokumentet är sant mot trädet vid `main`-spetsen med docs de-stale (MC 11200,
2026-10-24). Volatila antal — registernivå, floor-exempel, testcounts — hör hemma **daterade i
`LEDGER.md`**, inte i denna fil; här beskrivs endast mekaniken. Produktens status är **TESTED**:
hela testsviten grön (aktuell räknas av kört: `python3 -m pytest tests/ -q`), guarderna är
planted-red-gated, och den första fullskaliga publiceringen publicerades 2026-10-17 (commit
90e55f3) — **cutovern är stängd**; MC 10227:s mål (första fullskaliga nattkörningen) är
därmed levererat i trädet (board-bokföring tillhör orchestratorn).

## 1. Vad produkten är

Read-only **decision-support**: en statisk demosida med Rocket-score för **hela det trackeda
universumet** — registryn `source/rocket/data/universe_cache.json` (version 2; dess aktuella
nivå — entries, unika tickers, icke-tomma regioner och tidsstämpel — är **volatil** och
rekordas daterad i `LEDGER.md`; den flyttas bara genom `refresh_universe.py`), fetchad och
scoread **varje natt**. Sidans face: **Alla-fliken = topp 500 efter score**, därefter en flik
per region med alla scoreade tickers, samt ett filter som söker i **alla** paneler. Ingen
autotrading, ingen brokerkoppling, inga levande dataflöden mot läsaren — sidan är en snapshot
med öppen uppgift om både färskhet ("data hämtad …") och täckning ("scorerade N av M tickers i
registret"). Värd: **https://sibbamala.com/rocket/** (statisk nginx under alias `/rocket/`; rot
`apps/rocket` i hosting-monorepen, speglad från repo-root).

## 2. Dataflöde — den enda pipelinen

```
rocket-demo-publish.timer  (systemd user-unit, OnCalendar 05:00:00 UTC, källa source/systemd/)
 └─ rocket-demo-publish.service  (TimeoutStartSec=10800 — omräknat för hel-universum, MC 10220)
     └─ source/scripts/generate_demo_page.py
         entry-vakt  non-blocking flock på .demo-publish.lock (store-root): en andra
                     körning vägras högljutt, exit 1, zero writes (MC 11187);
                     --dry-run är READ-ONLY mot storen (facts ride, CSVs aldrig)
         0. registry  full_universe.load_plan — tecken-gate FÖRST (TICKER_RE/REGION_RE:
                      varje regionnyckel + ticker-entry måste klara allowlisterna, en
                      dålig registry VäGRAR körningen — inget tappas, MC 11184), sedan
                      S0-golv FÖRE nätverk: version==2,
                      m_unique ≥ 10 000, ≥8 icke-tomma regioner, trackerad cache ren,
                      krypningsankare m_unique ≥ 0.8× senast publicerad (C2-F5; ankaret
                      FINNS nu i committade stats — landade med första v3-publiceringen,
                      besiktigat i re-auditin 2026-10-23; utan ankare passerar kryptestet
                      vakuum per design: natt 1 kan inte bricka, C3-F3c)
         1. fetch     store_io-klassificering (saknas/skadad/>28 d → backfill 1y,
                      annars delta 1mo) → batchad bulk_fetcher._fetch_batch (50 per
                      batch, stride-planering C3-F1); in-progress-bar stryps vid
                      skrivning (§5 3a), split-kontinuitet 3b; {}-batch köas om en
                      gång → annars not_fetched (högljutt, ej tyst död); varje CSV
                      skrivs atomic PER SKRIVARE (mkstemp i samma dir + os.replace — den
                      fasta delade .tmp-filen rev en annan skrivares in-flight temp, MC 11187)
         2. score     score_all Pool(8) över alla storeade tickers — Ön är
                      scripts/nightly_score.score_row → compute_rocket_score
                      (34 indikatorer; confidence/risk/regime appliceras på
                      sidans score, MC 11474 "B"); TVÅ pass (D2): pass 1 alla
                      social=False, pass 2 preliminära topp-500 (ALLA_CAP)
                      social=True, adapterns 2 s timeout per anrop; C1: ≤4
                      REGION_INDEX-CVS:er läses EN gång per körning via
                      store_io (saknad fil = None = testad CHOP×0.5-fallback)
                      och följer med varje score_row-anrop; C3-sammanfogning
                      per ticker FÖRE results/partition; partition per §4 nedan
         3. indikatorer  VECKOVIS (lördag UTC): sample_backtest — deterministiskt
                      stratifierat 500-urval, Pool(12) över indikator-chunkar;
                      andra dagar: carry med ärlig cadence-notis (ej felspråk)
         4. render    demo_render — tabbar från UniversePlan.order, Alla = Top-500,
                      filter F3, färskhetsbanner över stabila bars; HTML-escaping i EN
                      samlingspunkt (_esc: html.escape quote=True) — varje dynamisk sträng
                      går dit, ingen undantags-väg (MC 11184)
         5. guard     fail-closed: golv scoreade rader max(2 500, ceil(0.40 × m_unique)) —
                      exakt heltals-CILT, banker's round borttagen: fail-open vid 49.66–
                      49.99 % är omöjligt (MC 11186); G6 per region ≥ ceil(0.5 ×
                      regionens registertotal); partition
                      sluten och not_fetched == 0; markörer för ALLA flikar
                      + v2-poängmarkören `id="score-v2"` (MC 11560 B3 — en
                      publicerad sida utan den failar guarden); sida
                      ≤ MAX_HTML_BYTES 2 500 000 (M_PAGE_MAX 16 113); stabila-bar-vakt
                      (monoton mot senaste gröna publicering, annars veckodagsregeln) —
                      annars skrivs INGET och gamla sidan står live
         6. publish   demo_publish — sync FÖRE write (git stash är borttaget), certifiering
                      på från disk återlästa bytes, commit + push av exakt två rötter,
                      ls-remote-bevis: PUBLISHED <sha> / PUBLISH-DIVERGED / PUBLISH-UNPROVEN
```

Misslyckas backtest-steget (3) på en lördag: senast commitade `indicator_stats.json`
**carry-forwardas** orört med felspråk; måndag–fredag är carryt *planerat* (cadence-notis, inget
felspråk — F5). Guarden i steg 5 är fixen mot att tomma eller avklippta sidor publicerats; ett
tyst återfall till en 35-raders sida är omöjligt och skulle synas som `scorerade 35 av M`.

**Räkenskaps-partition (DESIGN §4, C2-F2):** varje ticker hamnar i exakt en klass
(first-match-wins): `scored`, `dead_at_fetch`, `insufficient_rows`, `not_fetched`; summan måste
vara `m_unique` och `not_fetched` måste vara 0 (undantaget `--skip-fetch`).
`data_last_bar` = senaste **stabila** store-bar efter strip.

## 3. Moduler (under `source/`)

| Modul | Innehåll | Status |
|---|---|---|
| `rocket/technical/` | **34 indikatorer** + bas-kontraktet `BaseIndicator.calculate(df) → IndicatorResult` | levererad |
| `rocket/scoring/` | `rocket_score.py`: `INDICATORS` = **34** registrerade, `DIRECTION_INDICATORS` = **31** röstande, **3 risk-only**; `confidence.py`, `risk.py`, `filter.py` | levererad |
| `rocket/backtest/indicator_eval.py` | per-indicator-replay utan lookahead (oförändrad); körs nu veckovis över provet via `sample_backtest`, CLI:n lever kvar | levererad (MC 3874) |
| `rocket/data/universe_cache.json` | **trackerad registry** (version 2; aktuell nivå — entries/unika — volatil, daterad i `LEDGER.md`); den enda universum-källan | levererad |
| `rocket/data/universe_regions.py` | region-etiketter/tidszoner för 19 nycklar — enda sanningen för etiketter, återanvänds av loader och strip | återanvänd |
| `rocket/data/bulk_fetcher.py` | batchad hämtning (`_fetch_batch`, 50/batch) — återanvänds av nightly-fetchen | återanvänd |
| `scripts/full_universe.py` — ny — MC 10220 | registry-loader: primärregions-tilldelning (frusen prioritet), tab-ordning, tecken-gate (TICKER_RE/REGION_RE — MC 11184), S0-golv, **enda konstant-hemmet** (REGISTRY_MIN, MAX_HTML_BYTES → M_PAGE_MAX 16 113, ALLA_CAP 500), stride-batchning, seam C4 | ny |
| `scripts/store_io.py` — ny — MC 10220 | **enda skrivaren** till `source/data/raw/`: klassificering, atomic write per skrivare (mkstemp + os.replace, MC 11187), strip av in-progress-bar, split-kontinuitet, partitions-klassificerare | ny |
| `scripts/score_all.py` — ny — MC 10220 | Pool(8)-scoring över storen, noll ackumulering (685.3-läxan), F9-begränsad journal; **två pass (D2) genom ön** + C1-indexladdning EN gång per körning (MC 11474 B2) | ny → tvåpassar-via-ön MC 11474 |
| `scripts/nightly_score.py` — ny — MC 11474 B1 | adaptern: EN mappningsmyndighet store-frame → `compute_rocket_score`; `social` KRÄVD flagga, `N=2.0 s` timeout, options/fundamentals EXPLICIT OFF (D2/D3 choke point) | ny |
| `scripts/fetch_indices.py` — ny — MC 11545 B0 | ≤4 `REGION_INDEX`-index-CVS:er i stage 1 (efter `run_fetch`; C1: ägs av B0-hunken i `generate_demo_page.py`) | ny |
| `scripts/sample_backtest.py` — ny — MC 10220 | veckovis Indikatorer-omritning: deterministiskt 500-stratifierat urval, indikator-chunkar, begränsad merge-assertion | ny |
| `scripts/refresh_universe.py` — ny — MC 10220 | **enda** sanktionerade registry-uppdateraren (manuellt/orkestrerat, aldrig nattligen): gränsad krymp/växt + namngivna regioner + superset-regel; vägran lämnar filen orörd | ny |
| `scripts/generate_demo_page.py` | orkestrator **v3** (MC 10223): steg 0–6 ovan, entry-flock (MC 11187), `--dry-run` READ-ONLY mot storen/`--skip-fetch`/`--force-backtest`, run-manifest till `.tmp/` | omarbetad MC 10220 |
| `scripts/demo_render.py` | rendering **v3**: plan-genererade tabbar, Alla = topp 500, filter F3 (alla paneler), Universe-sektion = regionantal (ej varje ticker), HTML-escape i en `_esc`-samlingspunkt (MC 11184); **v2-poängmarkör + KÄND BEGRÄNSNING-rad** (social topp-500/2 s, social_score 0.0 tills tjänst shipas) MC 11560 B3 | omarbetad MC 10220 → v2-markör MC 11560 |
| `scripts/demo_publish.py` — ny — MC 10220 | publiceringsmekanik §6a: sync-före-skriv, self-clear, certifiering på lästa byte, ls-remote-bevis | ny |
| `scripts/demo_indicators.py` — ny — MC 10220 | vecko-gren: lör.-omritning vs planerat carry vs felet carry + åldertröskel 10 d (STDERR-flagga, blockerar ej) | ny |
| `app.py` | Tun DELEGAT till ön (`rocket_score.INDICATORS`); `_compute_all_indicators`/`_score_from_summary` återanvänder öns registry så det oserviceade Dash-UI:t inte kan bära en avvikande indikatoruppsättning (MC 11474 B2); `_REGION_MAP` resolverar **alla** registry-nycklar (F1) | ändrad MC 10220 → tunn delegat MC 11474 |
| *(35-ticker-modulen, MC 3874-eran)* | **UTGÅRD** — filen är borttagen (0105874): det hårdkodade 35-ticker-universumet är ersatt av registry-laddaren ovan; G4-testet förbjuder hårdkodade universum och återinförande av modulnamnet | utgången |
| `app.py`, `server.py` | Dash-dashboard — engine-UI:t, **serveras inte** | levererad |

## 4. Entrypoints

| Kommando (från repo-root) | Vad |
|---|---|
| `python3 source/scripts/generate_demo_page.py` | sidgeneratorn (v3) — körs av timern; flaggor `--dry-run`, `--skip-fetch`, `--force-backtest` |
| `python3 source/scripts/score_all.py [--workers N]` | manuell scoring-mätning över storen |
| `python3 source/scripts/refresh_universe.py [--commit]` | guardad registry-refresh — manuellt/orkestrerat, aldrig på timern |
| `PYTHONPATH=source python3 -m rocket.backtest.indicator_eval --cache-dir source/data/raw --out indicator_stats.json` | per-indicator-backtest från cache-CSV:er (nätverksfritt); i nightly-pipelinen körs den veckovis via `sample_backtest.py` |
| `python3 source/app.py` | Dash-UI lokalt (engine, inte served) |
| `engine.py`-CLI:n | legacy-scan/-backtest mot `signals.db` |

## 5. Schemaläggning

`rocket-demo-publish.timer` — **systemd user-unit**, `OnCalendar=*-*-* 05:00:00 UTC`,
`Persistent=true`; unit-filerna versioneras i `source/systemd/`. `TimeoutStartSec=10800`
(3 h) härlett i DESIGN §6 ur mätta fetch-/score-hastigheter (weekdag ≈ 72 min, lördag
inkl. veckobacktest ≈ 97 min — härlett; fullskaliga nattkörningar har körts sedan
cutovern stängdes — första fullskaliga publiceringen 90e55f3, 2026-10-17).
Historik: installation och aktivering verifierades under 35-eran (nattkörning 2026-10-01
05:00 UTC syns på den då serverade sidan); om-installation av user-uniten med 10800
efter merge är E4 (orchestrator) — utförd (LEDGER 2026-10-08, "unit re-install E4 done").

## 6. Data stores

- `source/data/raw/*.csv` — OHLCV-store, **en CSV per landad ticker** (antalet är
  volatilt — föl registry + storens historia; aktuell nivå daterad i `LEDGER.md`),
  gitignored
  (`.gitignore` `source/data/`), skrivs **bara** av `store_io`; ~1 år dagdata, enbart stabila
  bars; trasig fil läker genom backfill.
- `source/rocket/data/universe_cache.json` — trackerad registry (posten ovan); byggs
  aldrig nattligen — se `refresh_universe.py`.
- `source/data/signals.db` — legacy SQLite från engine-eran, gitignored och **finns inte i
  checkouten idag**; endast `engine.py`:s CLI-väg. Nightly rör den aldrig.
- Committade **root-artiklar**: `index.html` (regenereras nattligen), `indicator_stats.json`
  (committade filen är än så länge bara schema v1; **tilläggblocken** `registry` (skrivs av
  generatorns `_with_registry_anchor` vid varje v3-publicering — det är S0-krypningsankaret)
  och `sample` (skrivs av `sample_backtest.py` den v3-natt som kör sample-backtesten,
  bärs därefter vidare) — båda finns i den committade artifacten nu när v3-nattarna
  körts (båda besiktigade i re-auditin 2026-10-23)),
  `top25.json` (frozen snapshot).
- `.tmp/run_manifest.json` — kör-manifest (partition, per-region datum, timings); **ej
  committad**, scratch.
- Legacy-sidor som **serveras men saknar generator i repo** (`indicators.html`,
  `portfolio.html`, `region-top25.html` — för den sistnämnda finns numera ett
  Score-kolumns-regen-skript i repo, se §8.2, men ingen hel sidogenerator):
  **behålls** — deras URL:er är live; se §8.2.

## 7. Hosting-kontrakt

- **Portar: inga.** Statisk hosting, alias `/rocket/` på https://sibbamala.com; ingen
  process körs för sidan — den är committade filer.
- Serverat innehåll = **fem serverade sidor**: `index.html` (via `/rocket/`),
  `indicators.html`, `portfolio.html`, `region-top25.html`, `top25.json` — alla
  verifierade HTTP 200 2026-10-01.
- `source/` är versionerat men **inte** serverat; ingen `*.py` är nåbar via den
  serverade roten (`hosting.yaml` + monorepareglan "Files under source/ are versioned
  but NOT served").

## 8. Kända begränsningar

1. **Nattliga vägen är 34/34 genom ön (MC 11474 B2); Dash-UI:t i `app.py` är
   28/34 — omvandlaren är öppet follow-up-kort.** Sidans score går genom
   `scripts/nightly_score.score_row`, som applicerar
   `to_indicator_frame`-omvandlaren **före** `compute_rocket_score` — med den
   omvandlaren scorear alla **34** registrerade indikatorer felen (VERIFIED
   mot en riktig store-frame: 34/34, noll `KeyError`). Samma omvandlare
   appliceras i sample-vägen inuti löparen `rocket/backtest/indicator_eval.py`
   (`run_indicator_eval`; `sample_backtest.py` importerar den inte själv). Det
   oserviceade Dash-UI:t i `app.py` är nu en tunn delegat till öns registry,
   men i sin EGEN väg körs indikatorerna fortfarande på okonverterade
   lowercase-frames — då kastar de sex mönsterindikatorerna (`DoubleTopBottom`,
   `HeadShoulders`, `WedgePattern`, `AutoFractal`, `CupAndHandle`,
   `PatternDetectorCombined`) `KeyError` (VERIFIED: 28/34 på raw-lowercase).
   Dash-UI:t serveras inte och påverkar inte sidans publicerade score; att
   applicera omvandlaren i dess egen path är ett öppet follow-up-kort.
2. **Frozen snapshots:** `region-top25.html` + `top25.json` (samt `portfolio.html`) har
   ingen hel generator i repo och uppdateras inte — serveras som orörda snapshots
   (`region-top25.html`:s Score-kolumn har dock en regen-skript, se nedan).
   `region-top25.html`:s läge (re-auditin d7 + correcting 2026-10-23; LÖST av
   ägarbeslut "1c" 2026-10-24, MC 11188): sidan är nav-länkad och live-serverad;
   **Score-kolumnen regenererades 2026-10-24 från same-epoch `index.html`
   (epok 031b975)** med `source/scripts/regen_top25_scores.py` — 346 celler om­skrivna
   till visad precision, 309 med riktig poäng; 37 tickers utanför dagens scoreade
   mängd visar – och är listade i noten "Ej scoreade i dagens körr" under
   tabellerna — aldrig fake 0.00. Sidans egen provenance (header-raden och
   p-metat) ägs nu av samma skript och nämnar **båda epokerna**: Score =
   `index.html` 031b975 (2026-10-24); Price/Last date = 2026-09-08-snapshot
   — fryst, priserna följer INTE med regen (per-row-datumet är price-datum;
   en omskrivning till score-epoken skulle fake:a en kvot). De gamla
   claimen (`10300 scored`, parquet-sökvägen) greppas noll.
   Kolumnen hålls aktuell genom att köra om skriptet
   efter publicering (ingen nightly-timer — sidan förblir en snapshot).
   `top25.json` var och förblir INTE dess Score-källa (0 ticker-overlap, annan epok).
   `lang="sv"` är fixat (a934aa1).
3. **Snapshot-mekanik (accepterade residualer, DESIGN §13.8):** färskhetsbannern visar
   globalmax av stabila bars — en ensam efterbliven marknad syns inte (per-region-datum i
   run-manifestet är instrumentet); veckodagsregeln har ingen helgedagskalender (den
   monotona prev-green-grinden täcker natten); sidepayloaden projekteras ~1,5–2 MB och är
   takad vid 2,5 MB — gzip på värden (E2) och MC-alert-hook (E3) är escalerade off-repo.

## 9. Pensionerad vision — och vad som återinfördes (historik)

Föregångardokumentet (2026-07-24) planerade 25 000+ tickers, 10 år historik, chattbot
daily recs och options-/social-data-skalning. Den linjen pensionerades 2026-10-01 (MC 3874)
till förmån för en 35-ticker-snapshot. **2026-10-07 (MC 10220) återinförde ägaren
universumdelen av september-produkten** — full täckning på dagens layout, daglig uppdatering — med
garantier mot återfall (§2-golven). Resten av den gamla visionen förblir bortvald:
10-år historik (storen håller ~1 år), chattbot-dagsrecs, options-/social-scaling, autotrading.
`universe_builder.py` lever vidare **bakom** `refresh_universe`-garden (aldrig nattligen).
Chattbot-ärans död kod rederades batchvis av ett separat hygien-kort (MC 11199):
batch B1 (2026-10-24) raderade de sju scripten i `scripts/`, B2 bot-/Scan-Pro-klustret
och B3 (2026-10-24) dag-cron-kedjan i `rocket/` (7 filer: signalkedjans cron-orphans —
scoring, scan-cron, signalgenerator, universums-DB, portföljkoll och chattbot-ärans
gamla nattskanner). Namnkollisionen till trots: den raderade nattskannern är
INTE den levande produkten — nightly-pipelinen `rocket-demo-publish`
(`source/scripts/generate_demo_page.py`) är opåverkad och finns kvar som trädets enda
timerpar (MC 11200). B4 (scoring-slirarna), B5 (det gamla fetch-klustret) och B6
(2026-10-24: utils-paketet, de döda backtest-modulerna och rot-strays — 12 filer,
1 485 L) slutförde kortet — ingen död massa lämnas i `source/`. Inventeringen,
följarna och grep-noll-bevisen ligger i `.audits/20261023-0705-full-reaudit/`.

---

*Uppdaterad: 2026-10-28 (MC 11474 option-B wiring — docs-flip B3/B4-listan: §2 steg 2
skrivna till ön `nightly_score.score_row → compute_rocket_score` + två pass (D2) +
C1-indexladdning EN gång per körning + v2-poängsmarkören i steg 5; §3-modultabellen
fick rader för `nightly_score.py` + `fetch_indices.py` och `app.py`-raden skriven till
tun delegat; §8.1-punkten om 28/34 vänd — nattningsvägen är 34/34 genom ön (VERIFIED
34/34 på riktig store-frame), Dash-UI:t är 28/34 i sin EGEN omvandlare-lösa väg och är ett
öppet follow-up-kort; READMEs score-kedja + social-punkten fliprade till D2-gated +
KNOWN LIMIT. Tidigare samma dag: MC 11474 B2 wire-commit (se LEDGER). Tidigare:
2026-10-24 (MC 11199 batch B6 — terminal batch: utils-paketet, de döda
backtest-modulerna och rot-strays raderade (12 filer/1 485 L); §8-begränsningspunkten
om den raderade backtest-modulen borttagen och §8 omnumrerad (§8.3-hänvisningar → §8.2),
§9 och README:s död-kod-punkter skrivna till dåtid; de två dataquality-doksträngarna
som nämnde det döda driver-skriptet omformulerade — allt i samma commit. Tidigare samma
dag: MC 11199 batch B3: dag-cron-kedjan i `rocket/` raderad (7 filer),
§9 i samma commit; ruff.toml-per-fil-ignores följer med. Tidigare samma dag: MC 11199 batch B1: chattbot-era-scripten i `scripts/` och de
fyra vilande systemd-enheterna raderade; §9 omskriven. Tidigare samma dag: MC 11200 docs
de-stale: fix-wave-guarderna (ceil-golv, tecken-gate,
escape, store-lås, read-only dry-run) in skrivna; cutover-status uppdaterad (stängd 2026-10-17,
90e55f3); volatila antal utflyttade till LEDGER.md). Sanning prövas mot trädet: om
en uppräkning i denna fil motsägs av koden, är dokumentet fel — fixa dokumentet, inte
sanningen.*
