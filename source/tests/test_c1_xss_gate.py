"""C1 stored-XSS fix (MC 11184, d2-security F1): the fail-closed registry
gate + html.escape at every demo_render sink, with planted-bad reds and the
legitimate-ticker byte pins.

The two layers, both load-bearing:
  (b) loader gate — load_plan REFUSES any ticker entry outside
      ^[A-Z0-9._/-]{1,32}$ and any region key outside ^[a-z0-9._-]{1,32}$
      ('/' stays legal: the live registry carries 12 class shares BRK/A, BF/B,
      ... — refusing them would fail the REAL nightly, d2 F1 names them).
      This is what makes the onclick="showRegion('<reg>')" JS-string sink safe
      (html.escape alone does NOT protect that context — entities are
      decoded before the JS parser sees them).
  (a) renderer — html.escape at every dynamic sink, so a plan that reaches
      render() by another route (tests, future callers) still emits no
      executable markup.
Byte pins: the fixture page/table are rendered from fixed literal inputs
(no run-relative dates) and pinned by sha256 computed BEFORE the fix on
legitimate ASCII tickers (BRK/A, SAAB-B.ST, 0700.HK, digit-junk ...) — the
page must stay byte-identical across the escape change.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import fu_fixtures as fx           # noqa: E402
import full_universe as fu         # noqa: E402
import demo_render                 # noqa: E402

XSS_TICKER = "<img src=x onerror=1>"
ATTR_TICKER = 'BRK/A" onmouseover="alert(1)'
XSS_REGION = 'x"><script>alert(1)</script>'

PAGE_SHA = "098b0018f7c99ab973d4ec542831c99f16160edbd8b7c3f2d791838bdc90bc34"
TABLE_SHA = "a03e549dcbbfd2046851eb075b349523abdebc54292f714606a6ca08ccf632a1"


def _doc(**mutate):
    doc = fx.fixture_doc()
    for region, add in mutate.items():
        doc["tickers"][region] = list(doc["tickers"][region]) + add
    return doc


def _write(tmp_path, doc):
    p = tmp_path / "universe_cache.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _floors(monkeypatch):
    """Isolate the charset gate: the other floors must stay green."""
    monkeypatch.setattr(fu, "REGISTRY_MIN", 50)
    monkeypatch.setattr(fu, "MIN_REGIONS", 5)


# ── (b) loader gate: planted-bad reds (never silently drop) ─────────────────
def test_loader_refuses_xss_ticker(tmp_path, monkeypatch):
    _floors(monkeypatch)
    p = _write(tmp_path, _doc(usa=[XSS_TICKER]))
    with pytest.raises(fu.RegistryError, match="ticker rejected"):
        fu.load_plan(p)


def test_loader_refuses_attr_breakout_ticker(tmp_path, monkeypatch):
    _floors(monkeypatch)
    p = _write(tmp_path, _doc(usa=[ATTR_TICKER]))
    with pytest.raises(fu.RegistryError, match="ticker rejected"):
        fu.load_plan(p)


def test_loader_refuses_evil_region_key(tmp_path, monkeypatch):
    _floors(monkeypatch)
    doc = _doc()
    doc["tickers"][XSS_REGION] = ["EVILTICK1"]
    p = _write(tmp_path, doc)
    with pytest.raises(fu.RegistryError, match="region key rejected"):
        fu.load_plan(p)


def test_loader_refuses_nonstring_entry(tmp_path, monkeypatch):
    _floors(monkeypatch)
    p = _write(tmp_path, _doc(usa=[1965]))       # JSON number, not a string
    with pytest.raises(fu.RegistryError, match="ticker rejected"):
        fu.load_plan(p)


def test_loader_refuses_overlong_ticker_accepts_32(tmp_path, monkeypatch):
    _floors(monkeypatch)
    t33, t32 = "A" * 33, "A" * 32
    p = _write(tmp_path, _doc(usa=[t33]))
    with pytest.raises(fu.RegistryError, match="ticker rejected"):
        fu.load_plan(p)
    p = _write(tmp_path, _doc(usa=[t32]))
    assert t32 in fu.load_plan(p).regions["usa"]   # 32 chars: green boundary


def test_loader_class_shares_stay_legal(tmp_path, monkeypatch):
    """'/'-gate reading pinned: the 12 live class shares (BRK/A ...) load."""
    _floors(monkeypatch)
    p = _write(tmp_path, _doc(usa=["BRK/A", "BF/B"]))
    plan = fu.load_plan(p)
    assert {"BRK/A", "BF/B"} <= set(plan.regions["usa"])


def test_real_registry_loads_green(tmp_path):
    """Tracked universe_cache.json passes the gate — the whole nightly
    depends on it; guards the charset reading against over-tightening."""
    plan = fu.load_plan(git_check=False)          # READ-ONLY, no network
    assert plan.m_unique >= fu.REGISTRY_MIN


# ── (a) renderer: planted-bad reds ──────────────────────────────────────────
def _row(t, s="BUY", o=71.5):
    return {"ticker": t, "signal": s, "overall": o, "momentum": 60.0,
            "trend": 65.0, "volatility": 50.0, "volume": 55.0, "close": 10.5}


def _render(plan, results, excluded=()):
    return demo_render.render(plan, results, "2026-09-13 05:00 UTC",
                              "2026-09-11", None, None, excluded=excluded)


def _evil_plan():
    return fu.UniversePlan(
        regions={"usa": [XSS_TICKER, ATTR_TICKER], XSS_REGION: ["EVILTICK1"]},
        order=["usa", XSS_REGION], m_unique=3, dropped=[XSS_TICKER],
        registry_ts="2026-09-13T17:33:25+00:00\")><script>alert(9)</script>")


def test_renderer_no_executable_markup_from_planted_ticker():
    plan = _evil_plan()
    results = {"usa": [_row(XSS_TICKER), _row(ATTR_TICKER)],
               XSS_REGION: [_row("EVILTICK1", "SELL", 33.0)]}
    page = _render(plan, results, excluded=(XSS_TICKER,))
    # no planted string survives raw ...
    assert XSS_TICKER not in page
    assert ATTR_TICKER not in page
    # ... its escaped form renders as visible text ...
    assert "&lt;img src=x onerror=1&gt;" in page
    assert "BRK/A&quot; onmouseover=&quot;alert(1)" in page
    # ... and the planted <script>s never add to the ONE inline <script> the
    # page legitimately ships (the filter JS block).
    assert page.count("<script") == 1


def test_renderer_no_executable_markup_from_planted_region():
    plan = _evil_plan()
    results = {"usa": [_row(XSS_TICKER)], XSS_REGION: [_row("EVILTICK1")]}
    page = _render(plan, results)
    assert XSS_REGION not in page
    assert 'x&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert page.count("<script") == 1


def test_renderer_escapes_registry_ts():
    """Footer Registry <ts> is the same untrusted file as the tickers."""
    page = _render(_evil_plan(), {"usa": [_row("OK1")]})
    assert "\")><script>alert(9)</script>" not in page
    assert page.count("<script") == 1


# ── byte-identical pins: legitimate tickers unchanged across the fix ────────
PIN_PLAN = fu.UniversePlan(
    regions={"usa": ["BRK/A", "BF/B", "GOOGL", "1965"],
             "sweden": ["SAAB-B.ST", "ATCO-A.ST"],
             "germany": ["SAP.DE"],
             "hongkong": ["0700.HK", "0005.HK"],
             "japan": ["7203.T"]},
    order=["usa", "sweden", "germany", "hongkong", "japan"],
    m_unique=10, dropped=["Z-ONLY"],
    registry_ts="2026-09-13T17:33:25.510914+00:00")

PIN_RESULTS = {r: [_row(t, "SELL", 33.0) if i % 3 == 0 else _row(t)
                   for i, t in enumerate(ts)]
               for r, ts in PIN_PLAN.regions.items()}
# rows with non-10.5 closes: the pin must cover close formatting too
PIN_RESULTS["usa"][0]["close"] = 1287.5

PIN_DOC = {
    "schema_version": 1, "generated_at": "2026-09-12T04:00:00+00:00",
    "data_last_bar": "2026-09-11", "primary_horizon_days": 10,
    "engine": {"indicators_registered": 2, "tickers": 35, "warmup_bars": 60},
    "sample": {"tickers": 35, "method": "deterministic-stratified",
               "cadence": "weekly"},
    "indicators": {
        "ADX": {"name": "ADX", "category": "trend", "risk_only": False,
                "bars_evaluated": 190, "calc_errors": 0, "n_signals": 20,
                "n_buy": 12, "n_sell": 8, "evidence_weight": "moderate",
                "evidence_t": 1.63, "horizons": {"10": {
                    "long": {"n": 12, "hit_rate": 0.5833,
                             "avg_net_pct": 0.32},
                    "short": {"n": 8, "hit_rate": 0.375,
                              "avg_net_pct": -0.4}}}},
        "ATR": {"name": "ATR", "category": "volatility", "risk_only": True,
                "bars_evaluated": 190, "calc_errors": 3, "n_signals": 0,
                "n_buy": 0, "n_sell": 0, "evidence_weight": "insufficient",
                "evidence_t": None, "horizons": {"10": {
                    "long": {"n": 0, "hit_rate": None, "avg_net_pct": None},
                    "short": {"n": 0, "hit_rate": None,
                              "avg_net_pct": None}}}}}}

PIN_NOTE = ("Backtest fran 2026-09-12T04:00:00+00:00 bars vidare - denna "
            "lopnings backtest lyckades inte.")


def test_legit_page_byte_identical():
    page = demo_render.render(PIN_PLAN, PIN_RESULTS, "2026-09-13 05:00 UTC",
                              "2026-09-11", PIN_DOC, PIN_NOTE,
                              excluded=("BRK/A",))
    assert hashlib.sha256(page.encode()).hexdigest() == PAGE_SHA


def test_legit_table_byte_identical():
    tbl = demo_render._table([_row("BRK/A"), _row("SAAB-B.ST", "SELL", 33.0)],
                             "usa", label=fu.region_label("usa"))
    assert hashlib.sha256(tbl.encode()).hexdigest() == TABLE_SHA
