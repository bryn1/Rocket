# reason: generator v3 orchestration (DESIGN §6 stages 0-6) — the nightly
# entry point by mandate; past the 250 soft target, under the 400 hard
# ceiling (publish mechanics -> demo_publish.py, indicator branch ->
# demo_indicators.py).
#!/usr/bin/env python3
"""Generate the daily demo page (index.html) — generator v3 (MC 10223).

Stages (DESIGN §6): 0 registry loader (full_universe; S0 floors fire BEFORE
any network) -> 1 fetch (store_io classification + bulk_fetcher._fetch_batch
through the stride planner, C3-F1) -> 2 score_all Pool(8) + §4 partition
accounting -> 3 Indikatorer weekly branch (demo_indicators) -> 4 render -> 5
guards (§9 floor/G6/partition/markers/ceiling + C3-F3a settled gate) -> 6
publish (demo_publish: §6a sync-before-write, 2b, ls-remote proof).

--dry-run applies ALL floors before writing (F6); --skip-fetch renders from
the store (exempts §9 #3's not_fetched == 0 only); --force-backtest forces
the weekly redraw; ROCKET_FORCE_EMPTY_UNIVERSE=1 swaps in the EMPTY FIXTURE
PLAN — the deterministic guard red case. Run manifest .tmp/run_manifest.json
(scratch); the gate's prev-green bar reads only PUBLISHED entries.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "source"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_indicators  # noqa: E402
import demo_publish  # noqa: E402
import demo_render  # noqa: E402

INDEX_PATH = REPO_ROOT / "index.html"
STATS_PATH = REPO_ROOT / "indicator_stats.json"
DRY_DIR = REPO_ROOT / ".tmp"
MANIFEST_PATH = DRY_DIR / "run_manifest.json"
HARD_SCORED_FLOOR = 2_500     # §9 #1; fixtures patch these two constants
SCORED_FLOOR_FRACTION = 0.40
G6_FRACTION = 0.5             # §9 #2 per-region coverage


# ── stage 0: registry (T3 seam: full_universe owns floors + anchor math) ────

def _anchor_m_unique() -> int | None:
    """S0 shrink anchor from the committed indicator_stats.json "registry"
    field (C2-F5). Absent/unparseable -> None: the shrink check passes —
    first green publish writes it (C3-F3c bootstrap: night 1 cannot brick)."""
    try:
        reg = json.loads(STATS_PATH.read_text(encoding="utf-8")).get("registry")
    except (OSError, ValueError):
        return None
    n = reg.get("m_unique") if isinstance(reg, dict) else None
    return n if isinstance(n, int) else None


def load_universe_plan():
    """Stage 0 entry; every floor raises RegistryError BEFORE any network."""
    import full_universe
    return full_universe.load_plan(anchor_m_unique=_anchor_m_unique())


# ── stage 1: fetch (store_io + bulk_fetcher through the stride planner) ─────

def fetch_store(plan, *, skip_fetch, now_fn, cache_dir=None, fetcher=None,
                errors=None) -> dict:
    """DESIGN §5. Returns facts {ticker: {"batch_completed": bool}} for the §4
    partition. Batches = full_universe.make_batches STRIDE walk (C3-F1: never
    contiguous windows). A {} batch re-queues ONCE at end of stage; still {}
    -> members stay batch_completed=False (loud not_fetched) + stderr
    BATCH-EMPTY-OR-DEAD (§4 #1: {} is not delisting evidence). Split-break
    deltas re-fetch once as 1y replace (§5 3b). Failed fetches never touch
    existing CSVs."""
    facts = {t: {"batch_completed": False}
             for r in plan.order for t in plan.regions.get(r, [])}
    if skip_fetch or not facts:
        return facts
    errors = errors if errors is not None else []
    import full_universe
    import store_io
    if fetcher is None:
        from rocket.data.bulk_fetcher import _fetch_batch as fetcher
    from rocket.data.bulk_fetcher import BATCH_DELAY
    region_of = {t: r for r in plan.order for t in plan.regions.get(r, [])}
    today = now_fn().date()
    buckets: dict[str, list[str]] = {}
    for t, r in region_of.items():
        period, _, _ = store_io.classify(t, r, today, cache_dir=cache_dir)
        buckets.setdefault(period, []).append(t)
    requeue: list[tuple[list[str], str]] = []
    split_requeue: list[str] = []

    def absorb(batch, res, *, backfill: bool) -> None:
        for t in batch:
            facts[t]["batch_completed"] = True
        for t, frame in res.items():
            try:
                if not backfill:
                    stored = store_io.read_store(t, region_of[t],
                                                 cache_dir=cache_dir)
                    if store_io.split_break_detected(stored, frame):
                        split_requeue.append(t)
                        continue
                store_io.upsert(t, region_of[t], frame, cache_dir=cache_dir,
                                replace=backfill, now_fn=now_fn)
            except Exception as exc:  # noqa: BLE001 — store death != stage death
                errors.append(f"{t}: store: {exc}")

    def empty_note(batch) -> None:
        print(f"BATCH-EMPTY-OR-DEAD batch={','.join(batch[:3])} n={len(batch)}",
              file=sys.stderr)

    for period, tickers in sorted(buckets.items()):
        backfill = period == store_io.PERIOD_BACKFILL
        for batch in full_universe.make_batches(tickers):
            try:
                res = fetcher(batch, period)
            except Exception as exc:  # noqa: BLE001 — one batch must not kill the stage
                res = {}
                errors.append(f"batch[{','.join(batch[:3])}…]: {exc}")
            if res:
                absorb(batch, res, backfill=backfill)
            else:
                requeue.append((batch, period))
            time.sleep(BATCH_DELAY)
    for batch, period in requeue:            # ONE end-of-stage re-queue (§5)
        try:
            res = fetcher(batch, period)
        except Exception:  # noqa: BLE001
            res = {}
        if res:
            absorb(batch, res, backfill=period == store_io.PERIOD_BACKFILL)
        else:
            empty_note(batch)
    if split_requeue:                        # §5 3b: full 1y replaces the file
        for batch in full_universe.make_batches(sorted(split_requeue)):
            res = fetcher(batch, store_io.PERIOD_BACKFILL)
            if res:
                absorb(batch, res, backfill=True)
            else:
                empty_note(batch)
    return facts


# ── stage 2: scoring + §4 partition accounting ──────────────────────────────

def score_store(plan, *, cache_dir=None, workers=None) -> dict:
    """T3 seam: score_all.score_all(plan) -> results/meta/last_bars/scalars.
    Journal capped per F9 (counts + first 10 error strings)."""
    import score_all
    if not plan.order:
        return {"results": {}, "meta": {}, "last_bars": {}, "scalars": {
            "attempted": 0, "scored_regions": {}, "scored_total": 0,
            "error_count": 0, "errors_shown": [], "errors_truncated": 0}}
    out = score_all.score_all(plan, cache_dir=cache_dir,
                              workers=score_all.POOL_WORKERS
                              if workers is None else workers)
    print(score_all.summarize(out["scalars"]), file=sys.stderr)
    return out


def build_accounting(plan, scored: dict, fetch_facts: dict):
    """§4 partition via the pure classifier in store_io (first match wins).
    data_last_bar := max over stored settled bars (post-strip), §4."""
    import store_io
    assignments: dict[str, str] = {}
    for r in plan.order:
        for t in plan.regions.get(r, []):
            m = scored["meta"].get(t, {})
            assignments[t] = store_io.classify_accounting(
                produced_row=m.get("row") is not None,
                batch_completed=fetch_facts.get(t, {})
                .get("batch_completed", False),
                has_usable_store=bool(m.get("has_usable_store")))
    data_last_bar = store_io.data_last_bar(
        d for ds in scored["last_bars"].values() for d in ds)
    return assignments, data_last_bar


# ── stage 5: fail-closed guards (DESIGN §9) ─────────────────────────────────

def _markers(plan) -> list[str]:
    """Generated from UniversePlan.order — NEVER a literal region list."""
    return ([demo_render.BANNER_MARKER, demo_render.TAB_ALL_MARKER,
             demo_render.INDICATOR_TAB_MARKER, demo_render.PANEL_ALL_MARKER]
            + [f'{demo_render.PANEL_PREFIX}{r}"' for r in plan.order])


def _prev_green_bar() -> str | None:
    """data_last_bar of the last GREEN PUBLISH (C3-F3a). No manifest / never
    published -> None (loud note + weekday rule alone)."""
    try:
        doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not doc.get("published"):
        return None
    return doc.get("data_last_bar") or None


def _settled_gate_ok(data_last_bar: str, run_iso: str,
                    prev_green: str | None) -> bool:
    """§9 #6 settled-bar gate, C3-F3a correction: PASS when stored data has
    not regressed since the last green publish (monotonic freshness — covers
    holiday nights where the weekday rule, which has NO holiday calendar,
    false-reds), OR the weekday rule passes. Copy never claims holidays pass
    from the weekday rule alone."""
    if prev_green and data_last_bar and data_last_bar >= prev_green:
        return True
    if not prev_green:
        print("settled-bar gate: ingen publicerad run_manifest — prövar "
              "veckodagsregeln ensam (saknar helgedagskalender)",
              file=sys.stderr)
    return not demo_render.is_stale(run_iso, data_last_bar)


def _guard(html_text: str, plan, assignments: dict, scored_by_region: dict,
           data_last_bar: str, run_iso: str, prev_green: str | None, *,
           skip_fetch: bool = False) -> list[str]:
    """Fail-closed §9 checks; every reason names its subject. Any reason
    blocks EVERY write, dry-run included (F6)."""
    import full_universe
    import store_io
    reasons: list[str] = []

    total = sum(len(rows) for rows in scored_by_region.values())
    floor = max(HARD_SCORED_FLOOR, round(SCORED_FLOOR_FRACTION * plan.m_unique))
    if total < floor:
        reasons.append(f"endast {total} scorerade rader (golv {floor})")

    for reg in plan.order:                       # G6, per region, by name
        n_r = len(plan.regions.get(reg, []))
        s_r = len(scored_by_region.get(reg, []))
        g6_floor = round(G6_FRACTION * n_r)
        if s_r < 1 or s_r < g6_floor:
            reasons.append(f"G6 region {reg}: scorerade {s_r} av {n_r} "
                           f"(golv >= {max(1, g6_floor)})")

    reasons += store_io.accounting_reasons(assignments, plan.m_unique,
                                           skip_fetch=skip_fetch)

    bad = [m for m in _markers(plan) if m not in html_text]
    bad += [c for c in demo_publish.CONFLICT_MARKERS if c in html_text]
    for b in bad:
        reasons.append(f"marker saknas/ogiltig: {b}")

    page_bytes = len(html_text.encode())
    if page_bytes > full_universe.MAX_HTML_BYTES:
        reasons.append(f"PAGE-CAP: {page_bytes} B > MAX_HTML_BYTES="
                       f"{full_universe.MAX_HTML_BYTES} (M_PAGE_MAX="
                       f"{full_universe.M_PAGE_MAX}; escape: höj "
                       f"MAX_HTML_BYTES i en orchestrerad commit — aldrig "
                       f"render under taket)")

    if not _settled_gate_ok(data_last_bar, run_iso, prev_green):
        reasons.append(f"settled-bar gate: senaste stabila bar "
                       f"{data_last_bar or 'okänd'} (prev-green "
                       f"{prev_green or 'okänd'}; veckodagsregeln utan "
                       f"helgedagskalender slog)")
    return reasons


# ── stage 6 (delegation + manifest) ─────────────────────────────────────────

def publish(html_text: str, stats_text: str | None, markers: list[str]) -> str:
    """§6a mechanics live in demo_publish; this wrapper is the test seam."""
    return demo_publish.publish(html_text, stats_text, markers,
                                index_path=INDEX_PATH, stats_path=STATS_PATH,
                                repo_root=REPO_ROOT)


def _write_manifest(doc: dict) -> None:
    DRY_DIR.mkdir(exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _with_registry_anchor(doc: dict | None, plan) -> dict | None:
    """S0 anchor: additive 'registry' field on the published stats (C2-F5)."""
    if doc is None:
        return None
    return {**doc, "registry": {"m_unique": plan.m_unique,
                               "registry_ts": plan.registry_ts}}



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="render to .tmp/, write nothing in the root, no "
                             "git — ALL floors still apply (F6)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="render from the store; exempts §9 #3 "
                             "not_fetched == 0 only, never the floors")
    parser.add_argument("--force-backtest", action="store_true",
                        help="run the weekly Indikatorer redraw today (§8)")
    args = parser.parse_args()

    timings: dict[str, float] = {}
    errors: list[str] = []
    t0 = time.monotonic()
    if os.environ.get("ROCKET_FORCE_EMPTY_UNIVERSE") == "1":
        import full_universe
        plan = full_universe.UniversePlan()  # empty FIXTURE plan: red case
    else:
        try:
            plan = load_universe_plan()
        except Exception as exc:  # RegistryError et al — pre-network abort
            print(f"FAIL-CLOSED — registry: {exc}", file=sys.stderr)
            sys.exit(1)
    timings["registry"] = time.monotonic() - t0
    print(f"registry: {plan.m_unique} tickers · {len(plan.order)} regioner · ts {plan.registry_ts}")

    now_dt = datetime.now(timezone.utc)
    now_fn = lambda: now_dt                    # frozen clock seam for the run
    run_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_ts = now_dt.strftime("%Y-%m-%d %H:%M UTC")

    t = time.monotonic()
    fetch_facts = fetch_store(plan, skip_fetch=args.skip_fetch,
                              now_fn=now_fn, errors=errors)
    timings["fetch"] = time.monotonic() - t

    t = time.monotonic()
    scored = score_store(plan)
    timings["score"] = time.monotonic() - t
    assignments, data_last_bar = build_accounting(plan, scored, fetch_facts)

    t = time.monotonic()
    live = {r: [row["ticker"] for row in rows]
            for r, rows in scored["results"].items()}
    doc, note, bt_err = demo_indicators.stage(
        plan, force_backtest=args.force_backtest, now_dt=now_dt,
        stats_path=STATS_PATH, live_by_region=live)
    if bt_err:
        errors.append(f"backtest: {bt_err} (carry-forward)")
    demo_indicators.age_check(doc, now_dt)
    timings["indicators"] = time.monotonic() - t

    t = time.monotonic()
    html_text = demo_render.render(plan, scored["results"], run_ts,
                                   data_last_bar, doc, note)
    page_bytes = len(html_text.encode())
    rows = scored["scalars"]["scored_total"]
    print(f"PAGE-BYTES total={page_bytes} rows={rows} bytes_per_row="
          f"{(page_bytes / rows if rows else 0):.1f}", file=sys.stderr)
    stats_text = json.dumps(_with_registry_anchor(doc, plan), indent=2) \
        if doc is not None else None
    timings["render"] = time.monotonic() - t

    t = time.monotonic()
    prev_green = _prev_green_bar()
    reasons = _guard(html_text, plan, assignments, scored["results"],
                     data_last_bar, run_iso, prev_green,
                     skip_fetch=args.skip_fetch)   # ALL writes blocked (F6)
    timings["guard"] = time.monotonic() - t
    if reasons:
        print(f"FAIL-CLOSED — ingenting skrivet: {'; '.join(reasons)}",
              file=sys.stderr)
        sys.exit(1)

    manifest = {
        "run_iso": run_iso, "dry_run": args.dry_run,
        "skip_fetch": args.skip_fetch, "force_backtest": args.force_backtest,
        "m_unique": plan.m_unique, "regions": len(plan.order),
        "registry_ts": plan.registry_ts, "dropped": len(plan.dropped),
        "partition": dict(Counter(assignments.values())),
        "data_last_bar": data_last_bar,
        "prev_green_data_last_bar": prev_green,
        "region_last_bar": {r: max(ds) for r, ds in scored["last_bars"].items()
                            if ds},
        "page_bytes": page_bytes, "page_rows": rows,
        "indicators_generated_at": (doc or {}).get("generated_at"),
        "error_count": len(errors), "errors_shown": errors[:10],
        "timings": {k: round(v, 1) for k, v in timings.items()},
    }
    if args.dry_run:
        DRY_DIR.mkdir(exist_ok=True)
        (DRY_DIR / "index.html").write_text(html_text, encoding="utf-8")
        if stats_text is not None:
            (DRY_DIR / "indicator_stats.json").write_text(
                stats_text, encoding="utf-8")
        _write_manifest({**manifest, "published": None})
        print(f"Dry run — rendered {DRY_DIR / 'index.html'}: {rows} tickers, "
              f"{len(errors)} notes: {errors[:10]}")
        return

    sha = publish(html_text, stats_text, _markers(plan))
    _write_manifest({**manifest, "published": sha})
    print(f"Published to bryn1 main: {rows} tickers, {len(errors)} notes: "
          f"{errors[:10]}")


if __name__ == "__main__":
    main()
