# reason: generator v3 orchestration (DESIGN §6 stages 0-6) — the nightly
# entry point by mandate; past the 250 soft target, under the 400 hard
# ceiling (publish mechanics -> demo_publish.py, indicator branch ->
# demo_indicators.py, settled-bar gate -> demo_render.py, fetch stage ->
# fetch_store.py).
#!/usr/bin/env python3
"""Generate the daily demo page (index.html) — generator v3 (MC 10223).

Stages (DESIGN §6): 0 registry loader (full_universe; S0 floors fire BEFORE
any network) -> 1 fetch (store_io classification + bulk_fetcher.fetch_batch_typed
through the stride planner, C3-F1; per-member completion + rate-limit
backoff ladder live in fetch_store.py, MC 10309) -> 2 score_all Pool(8)
+ §4 partition accounting -> 3 Indikatorer weekly branch (demo_indicators)
-> 4 render -> 5 guards (§9 floor/G6/partition/markers/ceiling + C3-F3a
settled gate) -> 6 publish (demo_publish: §6a sync-before-write, 2b,
ls-remote proof).

--dry-run applies ALL floors before writing (F6) and is READ-ONLY against
the store (D8-R1 c: the fetch stage records facts, never upserts);
--skip-fetch renders from the store (exempts §9 #3's not_fetched == 0 only);
--force-backtest forces the weekly redraw; ROCKET_FORCE_EMPTY_UNIVERSE=1
swaps in the EMPTY FIXTURE PLAN — the deterministic guard red case. A non-
blocking flock on .demo-publish.lock (store root) serialises every entry
(D8-R1 b). .tmp/run_manifest.json (scratch): prev-green reads PUBLISHED
only — a green --dry-run writes its .dry sibling.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "source"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_indicators  # noqa: E402
import demo_publish  # noqa: E402
import demo_render  # noqa: E402
import excluded_tickers  # noqa: E402
from fetch_store import run_fetch as fetch_store  # stage 1; test seam name
from fetch_indices import run as fetch_indices  # B0 (MC 11545); seam-passed

INDEX_PATH = REPO_ROOT / "index.html"
STATS_PATH = REPO_ROOT / "indicator_stats.json"
DRY_DIR = REPO_ROOT / ".tmp"
MANIFEST_PATH = DRY_DIR / "run_manifest.json"
HARD_SCORED_FLOOR = 2_500     # §9 #1; fixtures patch these two constants
SCORED_FLOOR_FRACTION = 0.40
G6_FRACTION = 0.5             # §9 #2 per-region coverage

# ── D8-R1 (MC 11187): single-publisher lock — first act of every entry ──────

_lock_fd: int | None = None        # held for the PROCESS lifetime (flock owner)


def _store_lock_path() -> Path:
    env = os.environ.get("ROCKET_STORE_LOCK")    # test seam (fixture-env idiom)
    if env:
        return Path(env)
    import store_io
    return store_io.CACHE_DIR / ".demo-publish.lock"


def _acquire_publisher_lock() -> None:
    """Non-blocking flock on a lockfile in the store root — systemd only
    serialises the TIMER; the documented direct invocation had no guard
    (D8 R1: any second writer tears the store). Held => loud "another run
    in progress", exit 1, ZERO writes (runs before every stage). Same-
    process re-entry (tests call main() twice) reuses the held lock: the
    guard is CROSS-process (nightly vs manual); the fd is never closed, so
    the kernel releases it at exit."""
    global _lock_fd
    if _lock_fd is not None:
        return
    path = _store_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print(f"FAIL-CLOSED — another run in progress (publisher lock held: "
              f"{path})", file=sys.stderr)
        sys.exit(1)
    _lock_fd = fd


def _ceil_floor(fraction: float, n: int) -> int:
    """Exact integer ceil(n * fraction) — the ONLY floor math for the two
    §9 floors (C3, MC 11186). round() is banker's rounding and let both
    guards fail OPEN at 49.66-49.99 % (d3 F2). The float constant is read
    back as the human fraction it names (limit_denominator), then the ceil
    is pure integer math: (num*n + den - 1) // den — never a float."""
    f = Fraction(fraction).limit_denominator(1000)
    return (f.numerator * n + f.denominator - 1) // f.denominator


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

# Stage-1 body lives in fetch_store.py (MC 10309: the 400-line ceiling); the
# import above keeps the historical seam name `fetch_store` for callers and
# tests. Its module constants (RL_LADDER, COOLDOWN) are the backoff seams.


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
    """§9 #6 settled-bar gate (C3-F3a). The rule itself lives in demo_render
    beside is_stale — freshness rules keep ONE home (T13, MC 10264); this
    module-level name is the guard's and the tests' seam."""
    return demo_render.settled_gate_ok(data_last_bar, run_iso, prev_green)


def _guard(html_text: str, plan, assignments: dict, scored_by_region: dict,
           data_last_bar: str, run_iso: str, prev_green: str | None, *,
           skip_fetch: bool = False, excluded: list[str] = ()) -> list[str]:
    """Fail-closed §9 checks; every reason names its subject. Any reason
    blocks EVERY write, dry-run included (F6)."""
    import full_universe
    import store_io
    reasons: list[str] = []

    total = sum(len(rows) for rows in scored_by_region.values())
    floor = max(HARD_SCORED_FLOOR,
                _ceil_floor(SCORED_FLOOR_FRACTION, plan.m_unique))
    if total < floor:
        reasons.append(f"endast {total} scorerade rader (golv {floor})")

    for reg in plan.order:                       # G6, per region, by name
        n_r = len(plan.regions.get(reg, []))
        s_r = len(scored_by_region.get(reg, []))
        g6_floor = _ceil_floor(G6_FRACTION, n_r)
        if s_r < 1 or s_r < g6_floor:
            reasons.append(f"G6 region {reg}: scorerade {s_r} av {n_r} "
                           f"(golv >= {max(1, g6_floor)})")

    # MC 10062.139.22: tickers on the exclusion list (not_fetched 2+ nights)
    # leave the coverage gate; first-night misses and mass failure still fail.
    if excluded_tickers.too_many(len(excluded), plan.m_unique):
        reasons.append(f"exkluderade {len(excluded)} > "
                       f"{excluded_tickers.MAX_EXCLUDED_FRACTION:.1%} av "
                       f"{plan.m_unique} (batch/fetch-fel, inte avnoteringar)")
        excluded = ()
    skip = set(excluded)
    reasons += store_io.accounting_reasons(
        {t: c for t, c in assignments.items() if t not in skip},
        plan.m_unique - len(skip), skip_fetch=skip_fetch)

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


def _exclusions(assignments: dict, today: str, args) -> list[str]:
    """Update the exclusion state from tonight's not_fetched set (persisted
    BEFORE the guard so a fail-closed night still counts; --dry-run and
    --skip-fetch never touch it) and return the tickers excluded tonight."""
    import store_io
    nf = [t for t, c in assignments.items() if c == store_io.NOT_FETCHED]
    state = excluded_tickers.load()
    if args.dry_run or args.skip_fetch:
        return [t for t in excluded_tickers.excluded(state) if t in set(nf)]
    state = excluded_tickers.update(state, nf, today)
    excluded_tickers.save(state)
    return excluded_tickers.excluded(state)


# ── stage 6 (delegation + manifest) ─────────────────────────────────────────

def publish(html_text: str, stats_text: str | None, markers: list[str]) -> str:
    """§6a mechanics live in demo_publish; this wrapper is the test seam."""
    return demo_publish.publish(html_text, stats_text, markers,
                                index_path=INDEX_PATH, stats_path=STATS_PATH,
                                repo_root=REPO_ROOT)


def _write_manifest(doc: dict, *, dry: bool = False) -> None:
    DRY_DIR.mkdir(exist_ok=True)
    path = MANIFEST_PATH.with_name(f"{MANIFEST_PATH.stem}.dry.json") if dry \
        else MANIFEST_PATH               # F6 (MC 10264): dry runs never touch
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")   # the anchor


def _with_registry_anchor(doc: dict | None, plan) -> dict | None:
    """S0 anchor: additive 'registry' field on the published stats (C2-F5)."""
    if doc is None:
        return None
    return {**doc, "registry": {"m_unique": plan.m_unique,
                               "registry_ts": plan.registry_ts}}



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="render to .tmp/, no root writes, no git, store "
                             "READ-ONLY (MC 11187) — ALL floors apply (F6)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="render from the store; exempts §9 #3 "
                             "not_fetched == 0 only, never the floors")
    parser.add_argument("--force-backtest", action="store_true",
                        help="run the weekly Indikatorer redraw today (§8)")
    args = parser.parse_args()

    _acquire_publisher_lock()      # D8-R1 (b): FIRST act — held lock = no run

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
    fetch_stats: dict = {}                   # MC 10312 typed-capture stats
    fetch_facts = fetch_store(plan, skip_fetch=args.skip_fetch,
                              now_fn=now_fn, errors=errors, stats=fetch_stats,
                              writes_enabled=not args.dry_run)  # (c): dry = READ-ONLY
    # B0 (MC 11545, C1): regime index feed — walk the <=4 REGION_INDEX CSVs
    # through the SAME stage-1 seam right after the bar walk (fetch_fn passed
    # by reference, so the suite's fetch_store patch covers it). Inert
    # pre-B2: nothing supplies index_df until the wire commit. The throwaway
    # stats dict satisfies the out-param contract without touching the bar
    # walk's fetch_stats journal (the walk stays loud on its own stderr).
    fetch_indices(fetch_store, skip_fetch=args.skip_fetch, now_fn=now_fn,
                  errors=errors, stats={}, writes_enabled=not args.dry_run)
    timings["fetch"] = time.monotonic() - t
    if fetch_stats:                          # stderr journal, STARVED-style;
        tc = fetch_stats["typed_counts"]     # zero guard/accounting change
        print(f"TYPED-CAPTURE not_found={tc['not_found']} "
              f"rate_limited={tc['rate_limited']} "
              f"undecidable={tc['undecidable']} "
              f"landed={fetch_stats['landed']}/{fetch_stats['attempted']} "
              f"capture={fetch_stats['capture']}", file=sys.stderr)

    t = time.monotonic()
    scored = score_store(plan)
    timings["score"] = time.monotonic() - t
    assignments, data_last_bar = build_accounting(plan, scored, fetch_facts)
    excluded = _exclusions(assignments, run_iso[:10], args)

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
                                   data_last_bar, doc, note, excluded=excluded)
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
                     skip_fetch=args.skip_fetch,
                     excluded=excluded)   # ALL writes blocked (F6)
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
        _write_manifest({**manifest, "published": None}, dry=True)
        print(f"Dry run — rendered {DRY_DIR / 'index.html'}: {rows} tickers, "
              f"{len(errors)} notes: {errors[:10]}")
        return

    sha = publish(html_text, stats_text, _markers(plan))
    _write_manifest({**manifest, "published": sha})
    print(f"Published to bryn1 main: {rows} tickers, {len(errors)} notes: "
          f"{errors[:10]}")


if __name__ == "__main__":
    main()
