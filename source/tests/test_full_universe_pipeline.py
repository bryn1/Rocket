"""T5 (MC 10224): the full §9 planted-red pipeline suite — one guard per test,
each a TRUE red (guard goes RED, names its reason) beside its green pair,
driven through the INTEGRATED nightly (generate_demo_page stage seams) over the
shared fixtures (tests/fu_fixtures.py, tests/demo_page_fixtures.py — reused,
never re-implemented; the gen loader, _wire_main harness and the G4 ban regex
come from test_demo_page_generator itself). The §6a publish race matrix
(fake-remote git) lives in test_publish_matrix.py. Run-relative dates only
(C3-F3d); no network; no absolute 2026 calendar pins.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import fu_fixtures as fx                                   # noqa: E402,F401
import test_demo_page_generator as dtgen                   # noqa: E402
from demo_page_fixtures import (                           # noqa: E402
    FRESH_BAR, NOW, PLAN, SAMPLE_DOC, STALE_BAR, facts_done as _facts_done,
    iso as _iso, mk_frame as _mk_frame, mk_row as _mk_row)
import demo_render                                         # noqa: E402
import full_universe as fu                                 # noqa: E402
import store_io                                            # noqa: E402
from rocket.data.universe_regions import REGION_META       # noqa: E402

gen = dtgen.gen                    # the generator module (importlib-loaded)
_guard = dtgen._guard              # guard driver bound to the fixture PLAN
_html = dtgen._html                # fixture render driver
floors_patched = dtgen.floors_patched   # §9 floors via constants (G4 rule)


def scored_stub(counts=None, skip=()):
    """score_store double: per-region row lists (truncated/skipped), meta for
    the PRODUCED rows only (absent meta -> the §4 classifier decides thin /
    dead classes), FRESH last bars."""
    counts = counts or {}
    results = {r: [_mk_row(t) for t in
                   PLAN.regions[r][:counts.get(r, len(PLAN.regions[r]))]]
               for r in PLAN.order}
    for r in skip:
        results[r] = []
    meta = {row["ticker"]: {"row": row, "has_usable_store": True,
                            "last_bar": FRESH_BAR}
            for rows in results.values() for row in rows}
    return {"results": results, "meta": meta,
            "last_bars": {r: [FRESH_BAR] * len(rows)
                          for r, rows in results.items()},
            "scalars": {"attempted": PLAN.m_unique, "scored_regions": {
                r: len(v) for r, v in results.items()},
                "scored_total": sum(len(v) for v in results.values()),
                "error_count": 0, "errors_shown": [], "errors_truncated": 0}}


def synth_registry(tmp_path, regions=8, per=1250, version=2):
    """Synthetic >=10k registry driving the REAL floors (no floor patch):
    >= REGISTRY_MIN unique, >= MIN_REGIONS non-empty, version 2."""
    buckets = {f"r{i}x": [f"{chr(65 + i)}T{j:05d}" for j in range(per)]
               for i in range(regions)}
    p = tmp_path / "universe_cache.json"
    p.write_text(json.dumps({"tickers": buckets, "timestamp": fx.FIXTURE_TS,
                             "version": version}), encoding="utf-8")
    return p


def _stage0_env(monkeypatch, tmp_path, cache_path, stats_doc="missing"):
    """Point stage 0 at a fixture registry + stats anchor and wire a fetch
    SPY that raises: any network attempt detonates, so surviving proves
    the abort happened BEFORE any fetch."""
    monkeypatch.setattr(fu, "REGISTRY_PATH", cache_path)
    stats = tmp_path / "stats-anchor.json"
    if stats_doc != "missing":
        stats.write_text(json.dumps(stats_doc), encoding="utf-8")
    monkeypatch.setattr(gen, "STATS_PATH", stats)

    def spy(*a, **k):
        raise AssertionError("fetch reached — S0 must abort BEFORE any network")
    monkeypatch.setattr(gen, "fetch_store", spy)
    monkeypatch.delenv("ROCKET_FORCE_EMPTY_UNIVERSE", raising=False)
    monkeypatch.setattr(sys, "argv", ["gen", "--dry-run"])


# ── G2 registry-floor: 500 / version:1 / <8 regions ⇒ abort pre-fetch ──────

@pytest.mark.parametrize("cache_kwargs,needle", [
    (dict(regions=8, per=63), "m_unique 504 < REGISTRY_MIN"),
    (dict(regions=8, per=100, version=1), "registry version 1 != 2"),
    (dict(regions=6, per=1700), "non-empty regions < 8"),
])
def test_g2_registry_floor_aborts_before_any_fetch(monkeypatch, tmp_path,
                                                   capsys, cache_kwargs,
                                                   needle):
    _stage0_env(monkeypatch, tmp_path, synth_registry(tmp_path, **cache_kwargs))
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED — registry" in err and needle in err
    # fetch_store spy never called: it raises AssertionError, not SystemExit.


def test_g2_green_real_floors_load_and_no_network(monkeypatch, tmp_path):
    """Green pair: >=10k, >=8 regions, v2 loads with the REAL floors and
    the loader itself never touches the fetch seam."""
    _stage0_env(monkeypatch, tmp_path, synth_registry(tmp_path))
    plan = gen.load_universe_plan()          # no SystemExit, no spy detonation
    assert plan.m_unique == 10_000 and len(plan.order) == 8


# ── S0 anchor: bootstrap pass · equality pass · shrink named red ───────────

def test_s0_anchor_absent_bootstrap_and_equality_green(monkeypatch, tmp_path):
    _stage0_env(monkeypatch, tmp_path, synth_registry(tmp_path))
    # committed stats WITHOUT a registry field => shrink passes (C3-F3c):
    plan = gen.load_universe_plan()
    assert plan.m_unique == 10_000
    # blessed-worst-case equality (0.8 x 12500 == 10000) must NOT false-red:
    gen.STATS_PATH.write_text(json.dumps(
        {"registry": {"m_unique": 12_500, "registry_ts": fx.FIXTURE_TS}}),
        encoding="utf-8")
    assert gen.load_universe_plan().m_unique == 10_000


def test_s0_anchor_shrink_red_names_anchor(monkeypatch, tmp_path, capsys):
    _stage0_env(monkeypatch, tmp_path, synth_registry(tmp_path),
                stats_doc={"registry": {"m_unique": 20_000,
                                        "registry_ts": fx.FIXTURE_TS}})
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED — registry" in err and "shrink" in err \
        and "anchor 20000" in err


# ── G1 scored-floor: enforced in --dry-run (F6), dry bytes stable ──────────

def test_g1_dry_run_green_pairs_and_floor_below_040_red(monkeypatch, tmp_path,
                                                        capsys):
    dtgen._wire_main(monkeypatch, tmp_path)
    gen.main()                                # green dry pass first (F6 pair)
    dry = (tmp_path / "dry" / "index.html").read_bytes()
    assert b"scorerade 51 av 51" in dry       # av <fixture m> follows fixture
    # literal §9 G1 red: scored stubbed < 0.40 x M = round(0.4*51) = 20:
    monkeypatch.setattr(gen, "score_store",
                        lambda plan, **k: scored_stub(counts={"usa": 12},
                                                      skip=tuple(PLAN.order[1:])))
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED" in err and "scorerade rader (golv 20)" in err
    # floors run BEFORE the dry write (F6): dry file byte-stable, roots blank:
    assert (tmp_path / "dry" / "index.html").read_bytes() == dry
    assert not gen.INDEX_PATH.exists() and not gen.STATS_PATH.exists()


def test_g1_pure_floor_red_names_floor_number(monkeypatch, tmp_path, capsys):
    dtgen._wire_main(monkeypatch, tmp_path)
    monkeypatch.setattr(gen, "SCORED_FLOOR_FRACTION", 0.9)   # floor = 46
    monkeypatch.setattr(gen, "score_store", lambda plan, **k: scored_stub(
        counts={"usa": 15, "japan": 3}))     # 44 rows; every G6 floor intact
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED" in err and "scorerade rader (golv 46)" in err
    assert "G6 region" not in err            # the ONLY red reason is G1
    assert not (tmp_path / "dry" / "index.html").exists()  # nothing written


# ── G3 markers-every-tab: exact missing marker named; generated list ───────

def test_g3_green_render_has_every_fixture_panel_and_hongkong(floors_patched):
    html = _html()
    assert 'id="tab-hongkong"' in html and 'id="panel-hongkong"' in html
    for reg in PLAN.order:
        assert f'id="panel-{reg}"' in html, reg
    assert _guard(html=html, prev=FRESH_BAR) == []


def test_g3_surgical_removal_names_exact_markers(floors_patched):
    crippled = _html().replace('id="panel-hongkong"', 'id="panel-x"'
                              ).replace('id="panel-sweden"', 'id="panel-y"')
    reasons = _guard(html=crippled, prev=FRESH_BAR)
    assert 'marker saknas/ogiltig: id="panel-hongkong"' in reasons
    assert 'marker saknas/ogiltig: id="panel-sweden"' in reasons


def test_g3_markers_generated_from_plan_not_literal(floors_patched):
    grown = full_universe_plan(extra=(("brazil", ["BR1", "BR2", "BR3"]),))
    ms = gen._markers(grown)
    assert f'{demo_render.PANEL_PREFIX}brazil"' in ms
    assert len(ms) == 4 + len(grown.order)   # every tab, never a fixed list
    reasons = gen._guard(_html(), grown, _scored_assignments(grown),
                         _scored_for(grown), FRESH_BAR, _iso(NOW), FRESH_BAR)
    assert any('id="panel-brazil"' in r for r in reasons)


# ── G4 hardcoded-universe: static (reuse + extend ban) + behavioural ───────

def test_g4_static_no_hardcoded_universe_extended():
    dtgen.test_pipeline_has_no_hardcoded_universe()   # ban regexes reused
    import re
    for name in ("sample_backtest.py", "refresh_universe.py"):   # extension
        src = (TESTS.parent / "scripts" / name).read_text(encoding="utf-8")
        assert not re.search(r'^\s*UNIVERSE\s*[:=]', src, re.M), name
        assert not re.search(r'^\s*REGION_LABELS\s*[:=]', src, re.M), name
        assert '"usa": [' not in src, name
        assert not re.search(r'^\s*(import|from)\s+\S*demo_universe', src,
                             re.M), name


def test_g4_empty_universe_aborts_naming_hard_floor(tmp_path):
    """ROCKET_FORCE_EMPTY_UNIVERSE=1 (the empty FIXTURE plan) through the
    real CLI: unpatched floors refuse; nothing written anywhere."""
    def snap(p: Path):
        return p.read_bytes() if p.is_file() else b""
    root, dry_dir = gen.REPO_ROOT, gen.REPO_ROOT / ".tmp"
    dry_snap = lambda: {p.name: p.read_bytes()
                        for p in sorted(dry_dir.glob("*"))}   # noqa: E731
    before = (snap(root / "index.html"), snap(root / "indicator_stats.json"),
              dry_snap())
    env = {**os.environ, "ROCKET_FORCE_EMPTY_UNIVERSE": "1"}
    r = subprocess.run([sys.executable,
                        str(TESTS.parent / "scripts" / "generate_demo_page.py"),
                        "--dry-run", "--skip-fetch"],
                       cwd=str(TESTS.parent), env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "FAIL-CLOSED" in r.stderr
    assert "scorerade rader (golv 2500)" in r.stderr   # HARD_SCORED_FLOOR named
    assert "PAGE-BYTES total=" in r.stderr             # §9 #5 pin, red too
    assert (snap(root / "index.html"), snap(root / "indicator_stats.json"),
            dry_snap()) == before


# ── G5 HTML ceiling: exact MAX_HTML_BYTES boundary; refusal writes nothing ──

def test_g5_ceiling_boundary_exactly_at_bytes(floors_patched):
    base = _html()
    pad = fu.MAX_HTML_BYTES - len(base.encode())
    at_cap = base + "x" * pad               # exactly MAX_HTML_BYTES: passes
    assert _guard(html=at_cap, prev=FRESH_BAR) == []
    over = base + "x" * (pad + 1)           # one byte over: refuses
    cap = [r for r in _guard(html=over, prev=FRESH_BAR)
           if r.startswith("PAGE-CAP")]
    assert cap and f"{fu.MAX_HTML_BYTES + 1} B" in cap[0]
    assert f"MAX_HTML_BYTES={fu.MAX_HTML_BYTES}" in cap[0]
    assert f"M_PAGE_MAX={fu.M_PAGE_MAX}" in cap[0]      # §9 #5 constants named


def test_g5_pipeline_refuses_writing_anything(monkeypatch, tmp_path, capsys):
    dtgen._wire_main(monkeypatch, tmp_path)
    gen.main()
    dry = (tmp_path / "dry" / "index.html").read_bytes()
    big = _html() + "x" * (fu.MAX_HTML_BYTES - len(_html().encode()) + 1)
    monkeypatch.setattr(gen.demo_render, "render", lambda *a, **k: big)
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "PAGE-BYTES total=" in err                 # §9 #5 estimate pin
    assert "PAGE-CAP" in err and "M_PAGE_MAX=" in err
    assert (tmp_path / "dry" / "index.html").read_bytes() == dry


# ── G6 per-region coverage: named region reds, boundary green ──────────────

def test_g6_zero_hongkong_rows_fail_closed_naming_region(monkeypatch, tmp_path,
                                                         capsys):
    dtgen._wire_main(monkeypatch, tmp_path)
    monkeypatch.setattr(gen, "score_store",
                        lambda plan, **k: scored_stub(skip=("hongkong",)))
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED" in err and "G6 region hongkong" in err


def test_g6_partial_below_half_named_and_boundary_green(floors_patched):
    reasons = _guard(results=scored_stub(counts={"hongkong": 5})["results"],
                     prev=FRESH_BAR)         # 1 <= 5 < round(0.5*12) = 6
    hk = [r for r in reasons if "hongkong" in r]
    assert len(reasons) == 1 == len(hk) and "golv >= 6" in hk[0]
    assert _guard(results=scored_stub(counts={"hongkong": 6})["results"],
                  prev=FRESH_BAR) == []      # == half: green


# ── G-strip (§5 3a): frozen-clock hongkong strip — region-local, not UTC ───

def _hk_now(day: date, at_utc=None, at_local=None):
    tz = REGION_META["hongkong"]["timezone"]
    if at_utc is not None:
        inst = datetime.combine(day, at_utc, tzinfo=timezone.utc)
    else:
        inst = datetime.combine(day, at_local, tzinfo=ZoneInfo(tz))
    return lambda: inst


def test_gstrip_hongkong_strips_region_local_today_not_utc(tmp_path):
    """05:00 NEXT local day while it is still `day` UTC (HK = UTC+8): the
    in-progress bar is LOCAL today; the UTC-today bar is settled and stays."""
    # 21:00 UTC = 05:00 NEXT local day in HK: the row dated UTC-today is
    # settled; the LOCAL-today row (tomorrow) is the in-progress bar.
    frame = fx.mk_frame(days=3, end=fx.day_delta(1))     # D-1, D, D+1(local)
    store_io.upsert("HKG1", "hongkong", frame, cache_dir=tmp_path,
                    replace=True, now_fn=_hk_now(fx.today(), at_utc=time(21, 0)))
    stored = store_io.read_store("HKG1", "hongkong", cache_dir=tmp_path)
    dates = set(pd.to_datetime(stored["date"]).dt.date)
    assert dates == {fx.day_delta(-1), fx.today()}       # local today gone…
    assert fx.today() in dates         # …while the UTC-today bar stays put


def test_gstrip_close_guard_boundary_both_sides(tmp_path):
    frame = fx.mk_frame(days=3, end=fx.today())          # ends HK-local today
    d_minus = fx.day_delta(-2)
    store_io.upsert("HK1", "hongkong", frame, cache_dir=tmp_path, replace=True,
                    now_fn=_hk_now(fx.today(), at_local=time(18, 59)))
    early = set(pd.to_datetime(
        store_io.read_store("HK1", "hongkong", cache_dir=tmp_path)["date"]
    ).dt.date)
    store_io.upsert("HK1", "hongkong", frame, cache_dir=tmp_path, replace=True,
                    now_fn=_hk_now(fx.today(), at_local=time(19, 0)))
    late = set(pd.to_datetime(
        store_io.read_store("HK1", "hongkong", cache_dir=tmp_path)["date"]
    ).dt.date)
    assert early == {d_minus, fx.day_delta(-1)}          # <19:00: today gone
    assert late == early | {fx.today()}                  # >=19:00: kept


# ── G-partition (§9 #3): double-book, {}-batch, dead-at-fetch, identity ────

def test_gpartition_identity_closes_and_classes_are_exclusive():
    assignments, last = gen.build_accounting(PLAN, scored_stub(),
                                             _facts_done())
    assert last == FRESH_BAR
    assert len(assignments) == PLAN.m_unique
    from collections import Counter
    assert sum(Counter(assignments.values()).values()) == PLAN.m_unique
    assert set(assignments.values()) == {store_io.SCORED}


def test_gpartition_double_booked_plan_breaks_identity_guard(floors_patched):
    """Corrupt assignments (one ticker claimed by two regions): the ledger
    closes at 2, not the declared 3 -> the guard refuses the mismatch."""
    bad = full_universe_plan(regions={"usa": ["DUP", "A"],
                                      "sweden": ["DUP"]},
                             order=["usa", "sweden"], m_unique=3)
    scored = {"results": {"usa": [_mk_row("DUP"), _mk_row("A")],
                          "sweden": [_mk_row("DUP")]},
              "meta": {t: {"row": _mk_row(t), "has_usable_store": True}
                       for t in ("DUP", "A")},
              "last_bars": {"usa": [FRESH_BAR] * 2, "sweden": [FRESH_BAR]}}
    assignments, _ = gen.build_accounting(bad, scored, _facts_done(bad))
    assert len(assignments) == 2             # DUP collapsed: double-book lost
    reasons = gen._guard(_html(), bad, assignments, scored["results"],
                         FRESH_BAR, _iso(NOW), FRESH_BAR)
    assert any("partition not closed: 2 assigned != m_unique 3" in r
               for r in reasons)


def test_gpartition_empty_batch_after_requeue_named_and_dead_class(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """MC 10309 re-wire: dead_at_fetch means FRAME RETURNED, FILE MISSING
    (store death) — absence from a non-empty batch is STARVED -> honest
    NOT_FETCHED (test_fetch_rate_limit.py; the old code marked every member
    of a non-empty batch completed, which is the F-2 accounting lie)."""
    import rocket.data.bulk_fetcher as bf
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    # grouping only (dead-class needs a completed batch WITH a live sibling;
    # a <50-ticker stride plan is all singleton batches) — the stride/C3-F1
    # batching contract is tested in test_full_universe.py.
    monkeypatch.setattr(fu, "make_batches",
                        lambda ts, size=50: [ts[i:i + 2]
                                            for i in range(0, len(ts), 2)])
    plan = full_universe_plan(regions={"usa": ["A", "B"], "japan": ["J1"]},
                              order=["usa", "japan"], m_unique=3)
    real_upsert = store_io.upsert

    def upsert(ticker, region, frame, **kw):
        if ticker == "B":
            raise OSError("planted store death — frame returned, file lost")
        return real_upsert(ticker, region, frame, **kw)

    monkeypatch.setattr(store_io, "upsert", upsert)

    def fetcher(batch, period):              # japan batch: {} twice (dead);
        if all(t.startswith("J") for t in batch):
            return {}                        # B: frame returned, store dies
        return {t: _mk_frame() for t in batch}

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher)
    err = capsys.readouterr().err
    assert err.count("BATCH-EMPTY-OR-DEAD") == 1          # re-queue still {}
    scored = {"results": {"usa": [_mk_row("A")], "japan": []},
              "meta": {"A": {"row": _mk_row("A"), "has_usable_store": True,
                             "last_bar": FRESH_BAR}},
              "last_bars": {"usa": [FRESH_BAR], "japan": []}}
    assignments, _ = gen.build_accounting(plan, scored, facts)
    assert assignments["J1"] == store_io.NOT_FETCHED
    assert assignments["B"] == store_io.DEAD_AT_FETCH     # completed, no file
    assert len(assignments) == plan.m_unique
    reasons = store_io.accounting_reasons(assignments, plan.m_unique)
    assert any("not_fetched=1" in r and "J1" in r for r in reasons)


# ── Settled-bar gate C3-F3a: regression red · monotonic green · no-manifest ─

def test_settled_gate_regression_red_monotonic_green(floors_patched):
    assert gen._settled_gate_ok(STALE_BAR, _iso(NOW), FRESH_BAR) is False
    assert gen._settled_gate_ok(STALE_BAR, _iso(NOW), STALE_BAR) is True
    assert gen._settled_gate_ok(FRESH_BAR, _iso(NOW), FRESH_BAR) is True
    # WIRED: the regression must also reach the guard's reason list (the
    # mutation that drops the settled-gate branch goes RED here):
    assert any(r.startswith("settled-bar gate")
               for r in _guard(bar=STALE_BAR, prev=FRESH_BAR))
    assert _guard(bar=STALE_BAR, prev=STALE_BAR) == []      # Monday-holiday


def test_settled_gate_no_manifest_note_and_prev_green_reads_published_only(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")
    assert gen._prev_green_bar() is None                 # no manifest at all
    assert gen._settled_gate_ok(FRESH_BAR, _iso(NOW), None) is True
    assert "veckodagsregeln ensam" in capsys.readouterr().err
    assert gen._settled_gate_ok(STALE_BAR, _iso(NOW), None) is False  # weekday
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"published": None, "data_last_bar": FRESH_BAR}))   # dry run
    assert gen._prev_green_bar() is None
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"published": "a" * 40, "data_last_bar": FRESH_BAR}))
    assert gen._prev_green_bar() == FRESH_BAR


# ── helpers building UniversePlans without touching the loader ──────────────

def full_universe_plan(extra=(), regions=None, order=None, m_unique=None):
    import dataclasses
    base_regions = {r: list(v) for r, v in PLAN.regions.items()}
    if regions is not None:
        base_regions = regions
    for name, members in extra:
        base_regions[name] = list(members)
    return fu.UniversePlan(regions=base_regions,
                           order=list(order or PLAN.order
                                      + [n for n, _ in extra]),
                           m_unique=m_unique if m_unique is not None
                           else PLAN.m_unique
                           + sum(len(m) for _, m in extra),
                           dropped=list(PLAN.dropped),
                           registry_ts=PLAN.registry_ts)


def _scored_for(plan):
    return {r: [_mk_row(t) for t in plan.regions[r]] for r in plan.order}


def _scored_assignments(plan):
    return {t: store_io.SCORED for r in plan.order for t in plan.regions[r]}


# ── red/green transcript (build DoD; skipped in ordinary runs) ──────────────

TRANSCRIPT_DIR = os.environ.get("ROCKET_TRANSCRIPT_DIR")


@pytest.mark.skipif(not TRANSCRIPT_DIR,
                    reason="transcript run: set ROCKET_TRANSCRIPT_DIR")
def test_planted_red_transcript(monkeypatch, tmp_path, capsys):
    out = Path(TRANSCRIPT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    dtgen._wire_main(monkeypatch, tmp_path)
    gen.main()
    lines.append(f"GREEN dry-run: tabs {PLAN.order} + Alla + Indikatorer, "
                 "av 51 av 51, guard clean")
    monkeypatch.setattr(gen, "score_store",
                        lambda plan, **k: scored_stub(skip=("hongkong",)))
    try:
        gen.main()
    except SystemExit as e:
        lines.append(f"RED G6 (scored 0 hongkong rows, exit {e.code}): "
                     + capsys.readouterr().err.strip())
    assert lines[-1].startswith("RED G6"), lines[-1]
    (out / "pipeline_reds.txt").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
