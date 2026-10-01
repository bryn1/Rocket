"""Tests for the demo page generator v2 (MC 3874). No network: scoring and
the backtest runner are monkeypatched (seam C1 is imported lazily, so a fake
module in sys.modules drives it; seam C4 naming is pinned). The cache
round-trip is the planted-red proof of the V6 fix and the Title-case scoring
test the V4 fix — both fail pre-MC3874.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import demo_render  # noqa: E402
import demo_universe  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "generate_demo_page", SCRIPTS / "generate_demo_page.py")
gen = importlib.util.module_from_spec(spec)
sys.modules["generate_demo_page"] = gen
spec.loader.exec_module(gen)

SAMPLE_DOC = {
    "schema_version": 1, "generated_at": "2026-10-02T05:04:11Z",
    "data_last_bar": "2026-10-01", "primary_horizon_days": 10,
    "indicators": {
        "ADX": {"name": "ADX", "category": "trend", "risk_only": False,
                "bars_evaluated": 190, "calc_errors": 0, "n_signals": 20,
                "n_buy": 12, "n_sell": 8, "evidence_weight": "moderate",
                "evidence_t": 1.63, "horizons": {"10": {
                    "long": {"n": 12, "hit_rate": 0.5833, "avg_net_pct": 0.32},
                    "short": {"n": 8, "hit_rate": 0.375, "avg_net_pct": -0.4}}}},
        "ATR": {"name": "ATR", "category": "volatility", "risk_only": True,
                "bars_evaluated": 190, "calc_errors": 3, "n_signals": 0,
                "n_buy": 0, "n_sell": 0, "evidence_weight": "insufficient",
                "evidence_t": None, "horizons": {"10": {
                    "long": {"n": 0, "hit_rate": None, "avg_net_pct": None},
                    "short": {"n": 0, "hit_rate": None, "avg_net_pct": None}}}},
    }}

FAKE_UNIVERSE = {"usa": [f"T{i}" for i in range(20)],
                 "sweden": ["S1", "S2", "S3"], "germany": ["G1", "G2"]}


def _mk_frame():
    return pd.DataFrame({
        "date": pd.date_range("2026-09-22", periods=10),
        "open": [10.0] * 10, "high": [11.0] * 10, "low": [9.0] * 10,
        "close": [10.5] * 10, "volume": [1000.0] * 10})


def _mk_row(t):
    return {"ticker": t, "signal": "BUY", "overall": 71.0, "momentum": 60.0,
            "trend": 65.0, "volatility": 50.0, "volume": 55.0, "close": 10.5}


def _fake_eval_module(**kw):
    m = types.ModuleType("rocket.backtest.indicator_eval")
    m.to_indicator_frame = kw.get("to_indicator_frame", lambda df: df)
    m.run_indicator_eval = kw.get("run_indicator_eval",
                                 lambda f, u, **k: copy.deepcopy(SAMPLE_DOC))
    return m


def test_cache_filename_and_universe():
    assert demo_universe.cache_filename("SAAB-B.ST") == "SAAB_B_ST.csv"
    assert demo_universe.cache_filename("SAP.DE") == "SAP_DE.csv"
    assert set(demo_universe.UNIVERSE) == {"usa", "sweden", "germany"}
    assert sum(len(v) for v in demo_universe.UNIVERSE.values()) == 35


def test_cache_roundtrip_keeps_date_column(tmp_path):
    frame = pd.DataFrame(
        {"open": [10.0] * 10, "high": [11.0] * 10, "low": [9.0] * 10,
         "close": [10.5] * 10, "volume": [1000.0] * 10},
        index=pd.DatetimeIndex(pd.date_range("2026-09-22", periods=10),
                               name="date"))   # fetcher shape: date is index
    p = tmp_path / "X.csv"
    gen._write_cache(frame, p)
    assert p.read_text().splitlines()[0].startswith("date,")
    back = gen._read_cache(p)
    assert "date" in back.columns
    assert str(back["date"].dtype).startswith("datetime")
    assert str(back["date"].iloc[-1].date()) == "2026-10-01"


def test_read_cache_tolerates_legacy_shape(tmp_path):
    p = tmp_path / "old.csv"
    cols = ("open", "high", "low", "close", "volume")
    pd.DataFrame({c: [1.0, 2.0] for c in cols}).to_csv(p, index=False)
    back = gen._read_cache(p)
    assert "date" not in back.columns and len(back) == 2


def test_score_df_converts_via_to_indicator_frame(monkeypatch):
    seen = {}

    def converter(df):
        seen.setdefault("in", list(df.columns))
        return df.rename(columns=str.title)

    def fake_compute(df):
        seen.setdefault("scored", list(df.columns))
        return types.SimpleNamespace(signal="BUY", buy_count=1,
                                     sell_count=0), []

    fake_app = types.ModuleType("app")
    fake_app._compute_all_indicators = fake_compute
    fake_app._score_from_summary = lambda s, ticker, region: {
        "rocket_score": types.SimpleNamespace(
            overall_score=71.0, momentum_score=60.0, trend_score=65.0,
            volatility_score=50.0, volume_score=55.0)}
    monkeypatch.setitem(sys.modules, "rocket.backtest.indicator_eval",
                        _fake_eval_module(to_indicator_frame=converter))
    monkeypatch.setitem(sys.modules, "app", fake_app)
    row = gen._score_df("X", _mk_frame(), "usa")
    assert "close" in seen["in"]        # converter got the lowercase frame
    assert "Close" in seen["scored"]    # scorer ran on the Title-case frame
    assert row["close"] == 10.5 and row["overall"] == 71.0


def _sha(p: Path) -> bytes:
    return p.read_bytes() if p.exists() else b""


def test_fail_closed_guard_red_case():
    before = (_sha(gen.INDEX_PATH), _sha(gen.STATS_PATH))
    env = {**os.environ, "ROCKET_FORCE_EMPTY_UNIVERSE": "1"}
    r = subprocess.run([sys.executable,
                        str(SCRIPTS / "generate_demo_page.py"),
                        "--dry-run", "--skip-fetch"],
                       cwd=str(gen.REPO_ROOT / "source"), env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0, f"guard accepted empty universe: {r.stdout}"
    assert "FAIL-CLOSED" in r.stderr
    assert (_sha(gen.INDEX_PATH), _sha(gen.STATS_PATH)) == before


def _wire_run(monkeypatch, tmp_path, eval_mod, dry=True):
    monkeypatch.setattr(gen, "UNIVERSE", FAKE_UNIVERSE)
    monkeypatch.setattr(gen, "DRY_DIR", tmp_path / "dry")
    monkeypatch.setattr(gen, "INDEX_PATH", tmp_path / "root-index.html")
    monkeypatch.setattr(gen, "STATS_PATH", tmp_path / "root-stats.json")
    monkeypatch.setattr(gen, "publish", lambda: None)
    monkeypatch.setattr(gen, "process_ticker",
                        lambda t, r, s: (_mk_row(t), _mk_frame(), None))
    monkeypatch.setitem(sys.modules, "rocket.backtest.indicator_eval", eval_mod)
    monkeypatch.setattr(sys, "argv", ["gen", "--skip-fetch"]
                        + (["--dry-run"] if dry else []))


def test_green_dry_run_writes_tmp_files_only(monkeypatch, tmp_path):
    calls = {}

    def runner(f, u, **k):
        calls.update(frames=len(f), universe=u)
        return copy.deepcopy(SAMPLE_DOC)

    _wire_run(monkeypatch, tmp_path,
              _fake_eval_module(run_indicator_eval=runner))
    gen.main()
    html = (tmp_path / "dry" / "index.html").read_text()
    assert 'id="tab-indicators"' in html and 'id="freshness"' in html
    art = json.loads((tmp_path / "dry" / "indicator_stats.json").read_text())
    assert art["schema_version"] == 1
    assert calls == {"frames": 25, "universe": FAKE_UNIVERSE}
    assert not (tmp_path / "root-index.html").exists()
    assert not (tmp_path / "root-stats.json").exists()   # root untouched


def test_carry_forward_non_dry_keeps_previous_artifact(monkeypatch, tmp_path):
    stats = tmp_path / "root-stats.json"
    old = copy.deepcopy(SAMPLE_DOC) | {"generated_at": "2026-01-05T05:04:11Z"}
    stats.write_text(json.dumps(old, indent=2))
    before = stats.read_bytes()

    def boom(f, u, **k):
        raise RuntimeError("runner exploded")

    _wire_run(monkeypatch, tmp_path,
              _fake_eval_module(run_indicator_eval=boom), dry=False)
    monkeypatch.setattr(gen, "STATS_PATH", stats)
    gen.main()
    assert stats.read_bytes() == before                   # byte-identical
    html = (tmp_path / "root-index.html").read_text()
    assert "2026-01-05T05:04:11Z" in html                 # embedded, visible
    assert "bars vidare" in html


def test_unknown_schema_raises_or_treated_absent(monkeypatch, tmp_path):
    mod = _fake_eval_module(run_indicator_eval=lambda f, u, **k: {
        "schema_version": 99})
    monkeypatch.setitem(sys.modules, "rocket.backtest.indicator_eval", mod)
    with pytest.raises(ValueError, match="schema"):
        gen.run_backtest({"A": _mk_frame()}, {"usa": ["A"]})
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema_version": 7,
                             "generated_at": "2025-01-01T00:00:00Z"}))
    monkeypatch.setattr(gen, "STATS_PATH", p)
    doc, note = gen._load_carried_stats()
    assert doc is None and "2025-01-01T00:00:00Z" in note
    assert "Backtest saknas" in note


def test_render_smoke_markers_and_no_nan():
    results = {r: [_mk_row(t) for t in ts] for r, ts in FAKE_UNIVERSE.items()}
    html = demo_render.render(results, "2026-10-02 05:00 UTC", "2026-10-01",
                              SAMPLE_DOC, None)
    for marker in ('id="tab-indicators"', 'id="freshness"', 'id="panel-usa"',
                   'id="panel-sweden"', 'id="panel-germany"',
                   "Data hämtad", "Senaste kursdatum: 2026-10-01",
                   "Hit-rate (10d)", "Evidence-vikt", "indicators.html",
                   'role="tablist"', 'role="tab"', 'role="tabpanel"'):
        assert marker in html, marker
    assert "NaN" not in html and "None" not in html and "nan" not in html
    assert "risk-badge" in html             # ATR risk-only marker
    assert "beräkningsfel: 3" in html       # calc_errors surfaced
    assert "Backtest saknas" not in html    # doc present -> no empty text
    assert gen._guard(html, 25, FAKE_UNIVERSE) == []
    assert gen._guard(html, 3, FAKE_UNIVERSE)          # rows floor bites


def test_render_empty_backtest_state_and_stale_banner():
    results = {"usa": [_mk_row("A")]}
    html = demo_render.render(results, "2026-10-03 05:00 UTC", "2026-10-01",
                              None, None)
    assert "Backtest saknas — publiceras när närmaste löpning lyckats" in html
    assert 'class="sub stale"' in html      # run - bar > 26 h
    fresh = demo_render.render(results, "2026-10-02 00:00 UTC", "2026-10-01",
                               None, None)
    assert 'class="sub stale"' not in fresh and 'id="freshness"' in fresh
    assert demo_render.is_stale("2026-10-02T05:00:00", "2026-10-01") is True
    assert demo_render.is_stale("2026-10-02T01:00:00", "2026-10-01") is False
    assert demo_render.is_stale("2026-10-02T05:00:00", "") is False


def test_render_is_deterministic():
    results = {r: [_mk_row(t) for t in ts] for r, ts in FAKE_UNIVERSE.items()}
    args = (results, "2026-10-02 05:00 UTC", "2026-10-01", SAMPLE_DOC, None)
    assert demo_render.render(*args) == demo_render.render(*args)
    b = demo_render.render(results, "2026-10-20 05:00 UTC", *args[2:])
    assert demo_render.render(*args) != b   # explicit inputs drive output
