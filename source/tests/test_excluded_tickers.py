"""MC 10062.139.22 — exclusion list for tickers not fetched 2+ nights (owner
ruling 2026-10-16). Two-sided: listed tickers pass the coverage gate and show
in the footer; fresh/first-night misses and mass failure stay FAIL-CLOSED."""
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_render  # noqa: E402
import excluded_tickers as ex  # noqa: E402
from demo_page_fixtures import (  # noqa: E402
    FRESH_BAR, NOW, PLAN, assignments as _assignments,
    scored_by_region as _scored)

spec = importlib.util.spec_from_file_location(
    "generate_demo_page_ex", SCRIPTS / "generate_demo_page.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

TODAY = NOW.strftime("%Y-%m-%d")
YDAY = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
FOUR = ["T0", "T1", "T2", "T3"]
ARGS = SimpleNamespace(dry_run=False, skip_fetch=False)


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "excluded_tickers.json"
    monkeypatch.setattr(ex, "STATE_PATH", p)
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)
    return p


def _guard(nf, excluded):
    html = demo_render.render(PLAN, _scored(), NOW.strftime("%Y-%m-%d 05:00 UTC"),
                              FRESH_BAR, None, None, excluded=excluded)
    return html, gen._guard(
        html, PLAN, _assignments(nf=nf), _scored(), FRESH_BAR,
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), FRESH_BAR, excluded=excluded)


def test_a_listed_tickers_pass_and_footer_names_them(state_path, monkeypatch):
    monkeypatch.setattr(ex, "MAX_EXCLUDED_FRACTION", 0.5)   # fixture m is 51
    state_path.write_text(json.dumps({t: {
        "first_seen": YDAY, "last_seen": YDAY, "nights": 1,
        "reason": ex.REASON} for t in FOUR}))
    got = gen._exclusions(_assignments(nf=FOUR), TODAY, ARGS)
    assert got == FOUR and json.loads(state_path.read_text())["T0"]["nights"] == 2
    html, reasons = _guard(FOUR, got)
    assert reasons == []
    assert "4 tickers excluded (not fetched 2+ nights, e.g. T0, T1)" in html


def test_b_fifty_fresh_not_fetched_fail_closed(state_path):
    nf = [f"HK{i}" for i in range(12)] + [f"T{i}" for i in range(20)] \
        + [f"I{i}" for i in range(9)] + [f"J{i}" for i in range(5)] \
        + ["S1", "S2", "S3", "G1"]                           # 50 tickers
    assert len(set(nf)) == 50
    html, reasons = _guard(nf, [])
    assert any("not_fetched=50" in r for r in reasons)
    assert "excluded" not in html.split('class="footer"')[1]


def test_c_first_night_fails_closed_and_records_nights_1(state_path):
    got = gen._exclusions(_assignments(nf=["T0"]), TODAY, ARGS)
    assert got == []
    assert json.loads(state_path.read_text())["T0"]["nights"] == 1
    _, reasons = _guard(["T0"], got)
    assert any("not_fetched=1: T0" in r for r in reasons)
    assert gen._exclusions(_assignments(nf=["T0"]), TODAY, ARGS) == []  # rerun
    assert json.loads(state_path.read_text())["T0"]["nights"] == 1


def test_fetched_again_is_removed_and_dry_run_never_writes(state_path):
    state_path.write_text(json.dumps({"T0": {
        "first_seen": YDAY, "last_seen": YDAY, "nights": 5, "reason": "x"}}))
    dry = SimpleNamespace(dry_run=True, skip_fetch=False)
    gen._exclusions(_assignments(), TODAY, dry)
    assert "T0" in json.loads(state_path.read_text())
    gen._exclusions(_assignments(), TODAY, ARGS)
    assert json.loads(state_path.read_text()) == {}


def test_mass_exclusion_still_fails_closed(state_path):
    _, reasons = _guard(FOUR, FOUR)       # 4/51 > 0.5% default
    assert any("exkluderade 4" in r for r in reasons)
    assert any("not_fetched=4" in r for r in reasons)
