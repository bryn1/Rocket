"""Tests for the demo page generator v3 (MC 10223). No network: the T3 stage
seams (full_universe loader, fetch, score_all) are monkeypatched; fixture
plan = full_universe.UniversePlan per the §9 contract (>=5 regions, named
`hongkong` with >10 members, a 6th region `india` the fetch stub never
returns) — shared fixtures in demo_page_fixtures.py, publish (§6a + step-2b)
tests in test_demo_publish.py. ALL fixture dates are RUN-RELATIVE (C3-F3d);
the only absolute pin is the golden-35 dict (it IS the f2c02f6 published
page) and it lives with the FROZEN fixture store it scores in
test_golden35_fixtures.py (MC 10304): reading the live nightly store made it
a daily time-bomb (that store moved MSFT 57.8 -> 59.8 on the 2026-10-08
refetch), so it is pinned to committed, byte-locked fixture bytes instead.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_indicators  # noqa: E402
import demo_render  # noqa: E402
import full_universe  # noqa: E402
import store_io  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "generate_demo_page", SCRIPTS / "generate_demo_page.py")
gen = importlib.util.module_from_spec(spec)
sys.modules["generate_demo_page"] = gen
spec.loader.exec_module(gen)

import demo_page_fixtures as fx  # noqa: E402,F401 (pin asserts run on import)
from demo_page_fixtures import (          # shared with test_demo_publish.py
    FRESH_BAR, GENERATED_AT, NOW, PLAN, SAMPLE_DOC,
    STALE_BAR, expected_bar_date as _expected_bar_date, html as _html,
    iso as _iso, mk_frame as _mk_frame, mk_row as _mk_row,
    scored_by_region as _scored_by_region, scored_out as _scored_out,
    assignments as _assignments, facts_done as _facts_done)


@pytest.fixture
def floors_patched(monkeypatch):
    """§9 floors derive from constants — fixtures patch them (the fixture m
    is 51, not 12,793). Returns the monkeypatch for added patches."""
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)
    monkeypatch.setattr(gen, "SCORED_FLOOR_FRACTION", 0.4)
    return monkeypatch


# ── seam C4 + store round-trip (re-pointed from demo_universe) ──────────────

def test_cache_filename_escapes_and_registry_pin():
    assert full_universe.cache_filename("SAAB-B.ST") == "SAAB_B_ST.csv"
    assert full_universe.cache_filename("SAP.DE") == "SAP_DE.csv"
    assert full_universe.cache_filename("BRK/A") == "BRK_A.csv"  # '/' too
    for t in ("SAAB-B.ST", "BRK/A", "SAP.DE"):
        stem = full_universe.cache_filename(t)[: -len(".csv")]
        assert not any(s in stem for s in "./")


def test_store_roundtrip_keeps_date_column(tmp_path):
    frame = pd.DataFrame(
        {"open": [10.0] * 10, "high": [11.0] * 10, "low": [9.0] * 10,
         "close": [10.5] * 10, "volume": [1000.0] * 10},
        index=pd.DatetimeIndex(pd.date_range("2026-09-22", periods=10),
                               name="date"))
    merged = store_io.upsert("X", "usa", frame, cache_dir=tmp_path,
                             replace=True, now_fn=lambda: NOW)
    p = tmp_path / "X.csv"
    assert p.read_text().splitlines()[0].startswith("date,")
    back = store_io.read_cache(p)
    assert "date" in back.columns and len(merged) == 10


def test_read_cache_tolerates_legacy_shape(tmp_path):
    p = tmp_path / "old.csv"
    pd.DataFrame({c: [1.0, 2.0] for c in
                  ("open", "high", "low", "close", "volume")}).to_csv(
        p, index=False)
    back = store_io.read_cache(p)
    assert "date" not in back.columns and len(back) == 2


# ── stage 1: fetch driver + §4 partition (6th region never fetched) ────────

def test_fetch_store_not_fetched_and_partition(tmp_path, monkeypatch, capsys):
    import rocket.data.bulk_fetcher as bf
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    plan = full_universe.UniversePlan(
        regions={"usa": ["A", "B"], "india": ["I1"]},
        order=["usa", "india"], m_unique=3)
    # T13 F3 batch-count stride: one backfill batch is [A, B, I1] together,
    # so the never-returned 6th-region member needs its OWN batch — A and B
    # get fresh stores (delta bucket); I1 stays missing (backfill bucket).
    for t in ("A", "B"):
        store_io.upsert(t, "usa", _mk_frame(), cache_dir=tmp_path,
                        replace=True, now_fn=lambda: NOW)

    def fetcher(batch, period):       # the 6th-region stub: never returns
        if all(t.startswith("I") for t in batch):
            return {}, {}             # (res, typed) — production seam shape
        return {t: _mk_frame() for t in batch}, {}

    errors: list[str] = []
    facts = gen.fetch_store(plan, skip_fetch=False,
                            now_fn=lambda: NOW, cache_dir=tmp_path,
                            fetcher=fetcher, errors=errors)
    assert facts["A"]["batch_completed"] and facts["B"]["batch_completed"]
    assert not facts["I1"]["batch_completed"]
    assert (tmp_path / "A.csv").exists() and not (tmp_path / "I1.csv").exists()
    assert "BATCH-EMPTY-OR-DEAD" in capsys.readouterr().err
    scored = {"results": {"usa": [_mk_row("A"), _mk_row("B")], "india": []},
              "meta": {t: {"row": _mk_row(t), "has_usable_store": True,
                           "last_bar": FRESH_BAR} for t in ("A", "B")},
              "last_bars": {"usa": [FRESH_BAR, FRESH_BAR], "india": []}}
    assignments, _ = gen.build_accounting(plan, scored, facts)
    assert assignments == {"A": store_io.SCORED, "B": store_io.SCORED,
                           "I1": store_io.NOT_FETCHED}


def test_skip_fetch_exempt_not_fetched_only():
    a = _assignments(nf=("I1",))
    assert any("not_fetched=1" in r for r in
               store_io.accounting_reasons(a, PLAN.m_unique))
    reasons = store_io.accounting_reasons(a, PLAN.m_unique, skip_fetch=True)
    assert not any("not_fetched" in r for r in reasons)


def test_split_requeue_fetch_death_is_fail_closed(tmp_path, monkeypatch,
                                                  capsys):
    """T13 F7 (MC 10264): the §5 3b split-re-queue fetch was the one fetcher
    call WITHOUT the stage's fail-closed vocabulary — a raising/aborting
    fetcher there died with a bare traceback (E3 grep miss). Now: any abort or
    exception prints the greppable FAIL-CLOSED line BEFORE exiting."""
    import rocket.data.bulk_fetcher as bf
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    monkeypatch.setattr(store_io, "split_break_detected",
                        lambda stored, fetched: True)   # the §5 3b trigger
    plan = full_universe.UniversePlan(regions={"usa": ["A", "B"]},
                                      order=["usa"], m_unique=2)
    store_io.upsert("A", "usa", _mk_frame(), cache_dir=tmp_path,
                    replace=True, now_fn=lambda: NOW)   # A -> delta bucket

    calls = []

    def fetcher(batch, period):
        calls.append((tuple(batch), period))
        if batch == ["A"] and period == store_io.PERIOD_BACKFILL:
            raise RuntimeError("yf.download died mid-requeue (planted)")
        return {t: _mk_frame() for t in batch}, {}

    with pytest.raises(SystemExit) as e:
        gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                        cache_dir=tmp_path, fetcher=fetcher, errors=[])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL-CLOSED" in err and "split" in err.lower()
    assert (tuple(["A"]), store_io.PERIOD_BACKFILL) in calls


# ── stage 5: guards (planted-red proofs) ────────────────────────────────────

def _html(**kw):
    doc = kw.get("doc", SAMPLE_DOC)
    return demo_render.render(PLAN, kw.get("results", _scored_by_region()),
                              NOW.strftime("%Y-%m-%d 05:00 UTC"),
                              kw.get("bar", FRESH_BAR), doc, kw.get("note"))


def _guard(html=None, assignments=None, results=None, bar=FRESH_BAR,
           prev=None, skip_fetch=False):
    return gen._guard(html or _html(), PLAN,
                      assignments if assignments is not None
                      else _assignments(),
                      results if results is not None else _scored_by_region(),
                      bar, NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), prev,
                      skip_fetch=skip_fetch)


def test_guard_green(floors_patched):
    assert _guard(prev=FRESH_BAR) == []


def test_guard_scored_floor(floors_patched):
    floors_patched.setattr(gen, "HARD_SCORED_FLOOR", 9_999)
    reasons = _guard()
    assert reasons and "scorerade rader (golv 9999)" in reasons[0]


def test_guard_scored_floor_equality_green_and_below_red(floors_patched):
    """T7-P3 (MC 10264): §9 #1 is `scored >= golv` — EQUALITY must PASS. The
    TEST-verdict mutation `total < floor` -> `total <= floor` survived every
    pre-Fix test; pinning the boundary BOTH sides kills it."""
    floors_patched.setattr(gen, "HARD_SCORED_FLOOR", PLAN.m_unique)
    floors_patched.setattr(gen, "SCORED_FLOOR_FRACTION", 0.0)
    full = _scored_by_region()
    assert sum(len(r) for r in full.values()) == PLAN.m_unique
    assert _guard(prev=FRESH_BAR, results=full) == []      # == floor: GREEN
    below = {r: (rows[:-1] if r == "usa" else rows)
             for r, rows in full.items()}
    reasons = _guard(prev=FRESH_BAR, results=below)
    assert any(f"scorerade rader (golv {PLAN.m_unique})" in r for r in reasons)


def test_guard_g6_names_hongkong_and_unfetched(floors_patched):
    reasons = _guard(results=_scored_by_region(skip=("hongkong",)))
    assert any("hongkong" in r for r in reasons)
    reasons = _guard(results=_scored_by_region(skip=("india",)))
    assert any("india" in r for r in reasons)


def test_guard_partition_names_tickers(floors_patched):
    reasons = _guard(assignments=_assignments(nf=("HK1", "J1")))
    assert any("not_fetched=2" in r and "HK1" in r for r in reasons)


def test_guard_missing_panel_marker_named_exactly(floors_patched):
    crippled = _html().replace('id="panel-hongkong"', 'id="panel-x"')
    reasons = _guard(html=crippled)
    assert 'marker saknas/ogiltig: id="panel-hongkong"' in reasons


def test_guard_page_ceiling_refuses_with_constants(floors_patched, monkeypatch):
    monkeypatch.setattr(full_universe, "MAX_HTML_BYTES", 1_000)
    reasons = _guard()
    cap = [r for r in reasons if r.startswith("PAGE-CAP")]
    assert cap and "MAX_HTML_BYTES=1000" in cap[0] and "M_PAGE_MAX=" in cap[0]


def test_guard_settled_bar_monotonic_passes_holiday_false_red(floors_patched):
    # C3-F3a: bar is stale on the weekday rule but NOT regressed since the
    # last green publish -> gate passes via the monotonic alternative.
    assert _guard(bar=STALE_BAR, prev=STALE_BAR) == []


def test_guard_settled_bar_regression_and_note(floors_patched, capsys):
    reasons = _guard(bar=STALE_BAR, prev=FRESH_BAR)
    assert any(r.startswith("settled-bar gate") for r in reasons)
    # Absent manifest: weekday rule alone + loud note (never silent).
    assert _guard(bar=FRESH_BAR, prev=None) == []
    assert "veckodagsregeln ensam" in capsys.readouterr().err


def test_fail_closed_guard_red_case_roots_stable():
    def sha(p):
        return p.read_bytes() if p.exists() else b""
    before = (sha(gen.INDEX_PATH), sha(gen.STATS_PATH))
    env = {**os.environ, "ROCKET_FORCE_EMPTY_UNIVERSE": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "generate_demo_page.py"),
                        "--dry-run", "--skip-fetch"],
                       cwd=str(gen.REPO_ROOT / "source"), env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0, f"guard accepted empty fixture plan: {r.stdout}"
    assert "FAIL-CLOSED" in r.stderr
    assert (sha(gen.INDEX_PATH), sha(gen.STATS_PATH)) == before


# ── main() dry-run: fixture plan, patched stage seams ───────────────────────

def _wire_main(monkeypatch, tmp_path, dry=True, doc=SAMPLE_DOC, note=None):
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)
    monkeypatch.setattr(gen, "SCORED_FLOOR_FRACTION", 0.4)
    monkeypatch.setattr(gen, "load_universe_plan", lambda: PLAN)
    monkeypatch.setattr(gen, "fetch_store",
                        lambda plan, **kw: _facts_done())
    monkeypatch.setattr(gen, "score_store", lambda plan, **kw: _scored_out())
    monkeypatch.setattr(gen.demo_indicators, "stage",
                        lambda plan, **kw: (doc, note, None))
    monkeypatch.setattr(gen, "DRY_DIR", tmp_path / "dry")
    monkeypatch.setattr(gen, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(gen, "INDEX_PATH", tmp_path / "root-index.html")
    monkeypatch.setattr(gen, "STATS_PATH", tmp_path / "root-stats.json")
    monkeypatch.setattr(gen, "publish",
                        lambda html, stats, markers: "f" * 40)
    monkeypatch.setattr(sys, "argv",
                        ["gen", "--skip-fetch"] + (["--dry-run"] if dry else []))


def test_green_dry_run_writes_tmp_files_only(monkeypatch, tmp_path):
    _wire_main(monkeypatch, tmp_path)
    gen.main()
    html = (tmp_path / "dry" / "index.html").read_text()
    assert 'id="freshness"' in html and 'id="tab-indicators"' in html
    art = json.loads((tmp_path / "dry" / "indicator_stats.json").read_text())
    assert art["schema_version"] == 1
    assert art["registry"] == {"m_unique": 51,
                               "registry_ts": PLAN.registry_ts}  # S0 anchor
    # T13 F6: a dry-run's diagnostics live in a SEPARATE dry manifest — the
    # settled-gate manifest.json is off-limits to dry (anchor test below).
    man = json.loads((tmp_path / "manifest.dry.json").read_text())
    assert man["published"] is None and man["m_unique"] == 51
    assert man["prev_green_data_last_bar"] is None
    assert not (tmp_path / "manifest.json").exists()  # anchor file untouched
    assert not (tmp_path / "root-index.html").exists()      # roots untouched
    assert not (tmp_path / "root-stats.json").exists()


def test_green_dry_run_never_clobbers_the_prev_green_anchor(monkeypatch,
                                                            tmp_path):
    """T13 F6 / ARCH T10 F-2: a green REAL publish sets the settled-gate
    anchor; a green --dry-run afterwards must not erase it — a later
    _prev_green_bar() still returns the last published run's bar."""
    _wire_main(monkeypatch, tmp_path, dry=False)
    gen.main()
    man = tmp_path / "manifest.json"
    anchor = man.read_bytes()
    assert gen._prev_green_bar() == FRESH_BAR        # anchor live after publish
    _wire_main(monkeypatch, tmp_path, dry=True)
    gen.main()                                       # green dry run
    assert man.read_bytes() == anchor                # byte-identical: not clobbered
    assert gen._prev_green_bar() == FRESH_BAR        # next night still sees it
    dry_doc = json.loads((tmp_path / "manifest.dry.json").read_text())
    assert dry_doc["published"] is None              # dry diagnostics on dry file


def test_non_dry_publishes_and_manifest_ratchets_prev_green(monkeypatch,
                                                            tmp_path):
    _wire_main(monkeypatch, tmp_path, dry=False)
    published = {}

    def fake_publish(html, stats, markers):
        published["html"] = html
        return "a" * 40
    monkeypatch.setattr(gen, "publish", fake_publish)
    gen.main()
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["published"] == "a" * 40
    assert man["data_last_bar"] == FRESH_BAR       # the gate's future prev-green


# ── stage 3: Indikatorer honest states (F5, §8) ─────────────────────────────

def test_carry_scheduled_note_has_no_failure_wording(tmp_path):
    p = tmp_path / "stats.json"
    p.write_text(json.dumps({**SAMPLE_DOC,
                             "generated_at": "2026-01-05T05:04:11Z"}))
    wed = NOW.replace(hour=5) + timedelta(days=(2 - NOW.weekday()) % 7)
    doc, note, err = demo_indicators.stage(PLAN, force_backtest=False,
                                           now_dt=wed, stats_path=p)
    assert doc and err is None
    assert note == ("Indikatorer uppdateras veckovis — senaste körning "
                    "2026-01-05T05:04:11Z")
    assert "lyckades inte" not in note


def test_carry_failed_note_keeps_failure_copy(tmp_path, monkeypatch):
    p = tmp_path / "stats.json"
    old = {**SAMPLE_DOC, "generated_at": "2026-01-05T05:04:11Z"}
    p.write_text(json.dumps(old))
    boom = types.ModuleType("sample_backtest")

    def raise_(plan, **kw):
        raise RuntimeError("runner exploded")
    boom.run_sample_backtest = raise_
    monkeypatch.setitem(sys.modules, "sample_backtest", boom)
    sat = NOW + timedelta(days=(5 - NOW.weekday()) % 7)
    doc, note, err = demo_indicators.stage(PLAN, force_backtest=False,
                                          now_dt=sat, stats_path=p)
    assert doc and "lyckades inte" in note and err == "runner exploded"
    assert doc["indicators"] == old["indicators"]      # byte-identical carry


def test_carry_absent_or_unknown_schema_is_honest(tmp_path):
    doc, note = demo_indicators.load_carried(tmp_path / "none.json",
                                             "scheduled")
    assert doc is None and note is None
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema_version": 7,
                             "generated_at": "2025-01-01T00:00:00Z"}))
    doc, note = demo_indicators.load_carried(p, "failed")
    assert doc is None and "2025-01-01T00:00:00Z" in note
    assert "Backtest saknas" in note


def test_stale_indicator_stats_age_marker(tmp_path, capsys):
    old = {**SAMPLE_DOC, "generated_at": _iso(NOW - timedelta(days=12))}
    demo_indicators.age_check(old, NOW)
    assert "STALE-INDICATOR-STATS age=12d" in capsys.readouterr().err
    demo_indicators.age_check(SAMPLE_DOC, NOW)         # fresh: silent
    assert "STALE-INDICATOR-STATS" not in capsys.readouterr().err


# ── render: full fixture page, Alla cap, F3 filter, determinism ─────────────

def test_render_full_fixture_page_markers_and_copy():
    html = _html()
    for marker in ('id="tab-all"', 'id="tab-indicators"', 'id="freshness"',
                   'id="panel-all"', 'id="panel-usa"', 'id="panel-sweden"',
                   'id="panel-germany"', 'id="panel-hongkong"',
                   'id="panel-india"', 'id="panel-japan"',
                   "Alla · topp 500 av 51",
                   "scorerade 51 av 51 tickers i registret · 6 regioner",
                   f"Registry {PLAN.registry_ts}",
                   "1 tickers utan primärregion",       # dropped, never lost
                   "Hitta en ticker",
                   "Statistik på 35 tickers (veckovis urval, senast",
                   'data-label="Hongkong"', 'data-label="Indien"',
                   "filterTable(this)", 'id="filter-status"', "hittades inte",
                   "Senaste kursdatum: " + FRESH_BAR, "Hit-rate (10d)",
                   "Evidence-vikt", "indicators.html", 'role="tablist"',
                   'role="tabpanel"'):
        assert marker in html, marker
    assert "NaN" not in html and "None" not in html and "nan" not in html
    assert ".table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}" \
        in html
    assert "risk-badge" in html and "beräkningsfel: 3" in html


def test_render_alla_panel_capped_at_500():
    big = {r: [_mk_row(f"{r}-{i}", overall=900 - i)
               for i in range(120 if r == "usa" else 100)]
           for r in PLAN.order}
    html = demo_render.render(PLAN, big, "run", FRESH_BAR, SAMPLE_DOC, None)
    assert "Alla · topp 500 av 620" in html      # 120 + 5*100 = 620 scored
    assert "Alla (500)</button>" in html


def test_render_indikatorer_failure_and_scheduled_notes():
    failed = _html(note="Backtest från X bars vidare — denna löpnings backtest "
                        "lyckades inte.")
    assert "lyckades inte" in failed and "Backtest saknas" not in failed
    sched = _html(note="Indikatorer uppdateras veckovis — senaste körning X")
    assert "veckovis" in sched and "lyckades inte" not in sched
    empty = demo_render.render(PLAN, _scored_by_region(), "run", FRESH_BAR,
                               None, None)
    assert demo_render.EMPTY_BACKTEST_TEXT in empty


def test_render_is_deterministic():
    args = (PLAN, _scored_by_region(), "2026-10-02 05:00 UTC", FRESH_BAR,
            SAMPLE_DOC, None)
    assert demo_render.render(*args) == demo_render.render(*args)
    assert demo_render.render(*args) != demo_render.render(
        PLAN, _scored_by_region(), "2026-10-20 05:00 UTC", *args[3:])


def test_render_empty_state_and_stale_banner():
    empty_plan = full_universe.UniversePlan(
        regions={"usa": [_mk_row("A")["ticker"]]}, order=["usa"], m_unique=1,
        dropped=[], registry_ts="")
    results = {"usa": [_mk_row("A")]}
    stale_run = (_expected_bar_date() + timedelta(days=3)
                 ).strftime("%Y-%m-%d")
    html = demo_render.render(full_universe.UniversePlan(
        regions={"usa": ["A"]}, order=["usa"], m_unique=1, dropped=[],
        registry_ts=""), results, f"{stale_run} 05:00 UTC",
        (NOW.date() - timedelta(days=9)).isoformat(), None, None)
    assert 'class="sub stale"' in html and 'id="freshness"' in html
    assert empty_plan.m_unique == 1


def test_is_stale_weekday_rule():
    """Rule unchanged (demo_render is THE rule). is_stale is PURE
    (run_iso, data_last_bar) -> bool, so explicit weekdays are deterministic,
    not calendar pins: 2026-10-05 Mon … 09 Fri, 10 Sat, 11 Sun, 12 Mon."""
    # Fresh — the nightly (prev calendar day) and weekend steps:
    assert demo_render.is_stale("2026-10-07T05:00:00", "2026-10-06") is False
    assert demo_render.is_stale("2026-10-10T05:00:00", "2026-10-09") is False
    assert demo_render.is_stale("2026-10-12T05:00:00", "2026-10-09") is False
    assert demo_render.is_stale("2026-10-11T05:00:00", "2026-10-09") is False
    # Stale — one full trading day missing, or older:
    assert demo_render.is_stale("2026-10-09T05:00:00", "2026-10-07") is True
    assert demo_render.is_stale("2026-10-12T05:00:00", "2026-10-08") is True
    assert demo_render.is_stale("2026-10-12T05:00:00", "2026-09-25") is True
    # Honest-unknown and bad input keep the not-stale behaviour:
    assert demo_render.is_stale("2026-10-12T05:00:00", "") is False
    assert demo_render.is_stale("not-a-date", "2026-10-09") is False


# ── scoring seam: F1 fix + golden 35 (§3) ───────────────────────────────────

def test_region_map_covers_every_meta_key():
    import app
    from rocket.data.models import Region
    from rocket.data.universe_regions import REGION_META
    app._assert_region_map(app._REGION_MAP, REGION_META)   # no raise
    for k in REGION_META:
        Region(app._REGION_MAP[k])                        # §3 test (a)
    for key, val in (("sweden", "smid"), ("usa", "us"), ("china", "asia"),
                     ("india", "asia"), ("germany", "eu")):
        assert app._REGION_MAP[key] == val                # kept unchanged


def test_future_unmapped_key_raises_loud_at_load():
    import app
    from rocket.data.universe_regions import REGION_META
    crippled = {k: v for k, v in app._REGION_MAP.items() if k != "hongkong"}
    with pytest.raises(RuntimeError, match=r"hongkong"):
        app._assert_region_map(crippled, REGION_META)


def test_score_from_summary_hongkong_resolves(tmp_path, monkeypatch):
    """F1 real-seam proof: with the key mapped a hongkong ticker scores;
    with it removed (the pre-fix map) Region('HONGKONG') raises — the class
    the per-ticker except used to swallow into 0 rows with a green guard."""
    import app
    from rocket.backtest.indicator_eval import to_indicator_frame
    idf = to_indicator_frame(_mk_frame(70))
    summary, _ = app._compute_all_indicators(idf)
    out = app._score_from_summary(summary, ticker="X", region="hongkong")
    assert out["rocket_score"] is not None
    monkeypatch.setattr(app, "_REGION_MAP",
                        {k: v for k, v in app._REGION_MAP.items()
                         if k != "hongkong"})
    with pytest.raises(ValueError):
        app._score_from_summary(summary, ticker="X", region="hongkong")


# §3 test (b) — the golden-35 continuity pin — lives in
# test_golden35_fixtures.py (MC 10304): it runs on the FROZEN committed
# fixture store fx.GOLDEN35_DIR, byte-locked by its SHA256SUMS manifest, and
# never on the live nightly store (which the nightly rewrites daily, so the
# pin there went RED every refetch: MSFT 57.8 -> 59.8 on 2026-10-08).


# ── G4 (static half): no hardcoded universe in pipeline sources ─────────────

def test_pipeline_has_no_hardcoded_universe():
    srcs = [SCRIPTS / n for n in ("generate_demo_page.py", "demo_render.py",
                                 "demo_publish.py", "demo_indicators.py")]
    srcs += [SCRIPTS.parent / "app.py", SCRIPTS / "score_all.py",
             SCRIPTS / "store_io.py", SCRIPTS / "full_universe.py"]
    for p in srcs:
        src = p.read_text(encoding="utf-8")
        assert not re.search(r'^\s*UNIVERSE\s*[:=]', src, re.M), p
        assert not re.search(r'^\s*REGION_LABELS\s*[:=]', src, re.M), p
        assert '"usa": [' not in src, p
        assert not re.search(r'^\s*(import|from)\s+\S*demo_universe', src,
                             re.M), p
    assert not (SCRIPTS / "demo_universe.py").exists()          # R1 deleted


# ── deterministic fixture dry-run transcript (DoD artifact) ─────────────────

TRANSCRIPT_DIR = os.environ.get("ROCKET_TRANSCRIPT_DIR")


@pytest.mark.skipif(not TRANSCRIPT_DIR,
                    reason="transcript run: set ROCKET_TRANSCRIPT_DIR")
def test_fixture_dry_run_transcript(monkeypatch, tmp_path):
    out = Path(TRANSCRIPT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    _wire_main(monkeypatch, tmp_path)
    gen.main()
    html = (tmp_path / "dry" / "index.html").read_text()
    for reg in PLAN.order:
        assert f'id="tab-{reg}"' in html and f'id="panel-{reg}"' in html, reg
    assert 'id="panel-hongkong"' in html and "Alla · topp 500 av 51" in html
    (out / "index.html").write_text(html, encoding="utf-8")
    log.append(f"GREEN fixture dry-run: {len(html.encode())} B, tabs "
               f"{PLAN.order} (+Alla+Indikatorer), manifest: "
               + json.dumps(json.loads(
                   (tmp_path / "manifest.json").read_text())))
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)
    monkeypatch.setattr(gen, "SCORED_FLOOR_FRACTION", 0.4)
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 9_999)
    before = (tmp_path / "dry" / "index.html").read_bytes()
    with pytest.raises(SystemExit):
        gen.main()
    log.append("RED G1 scored-floor: FAIL-CLOSED before the dry write "
               f"(dry file byte-stable: "
               f"{(tmp_path / 'dry' / 'index.html').read_bytes() == before})")
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)   # back low for red-proofs
    crippled = html.replace('id="panel-hongkong"', "id=panel-x")
    reasons = gen._guard(crippled, PLAN, _assignments(), _scored_by_region(),
                         FRESH_BAR, _iso(NOW), FRESH_BAR)
    log.append(f"RED G3 missing-marker: {[r for r in reasons if 'hongkong' in r]}")
    reasons = _guard(results=_scored_by_region(skip=("hongkong",)))
    log.append(f"RED G6 per-region: {[r for r in reasons if 'hongkong' in r]}")
    reasons = _guard(bar=STALE_BAR, prev=FRESH_BAR)
    log.append(f"RED settled-bar gate (regression vs prev-green): {reasons}")
    log.append(f"GREEN C3-F3a monotonic pass: "
               f"{_guard(bar=STALE_BAR, prev=STALE_BAR) == []}")
    (out / "dry_run_log.txt").write_text("\n".join(log) + "\n",
                                         encoding="utf-8")
