"""Shared fixtures for the demo-page v3 test modules (MC 10223) — same
idiom as fu_fixtures.py (T3). §9 fixture contract: >=5 regions; named
`hongkong` with >10 members; the 6th region `india` is the one the fetch
stub never returns (G6 red). The "35 tickers" pin of the demo era is
REPLACED by the registry-fixture pin below. Dates are RUN-RELATIVE
(C3-F3d): every fixture bar/date is computed from today, never pinned.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import full_universe  # noqa: E402
import store_io  # noqa: E402

NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_bar_date() -> date:
    """Run-relative expected settled bar under the weekday rule (fresh
    data): latest weekday strictly before today — mirrors is_stale."""
    d = NOW.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


FRESH_BAR = expected_bar_date().isoformat()
STALE_BAR = (expected_bar_date() - timedelta(days=5)).isoformat()
GENERATED_AT = iso(NOW - timedelta(hours=1))

SAMPLE_DOC = {
    "schema_version": 1, "generated_at": GENERATED_AT,
    "data_last_bar": FRESH_BAR, "primary_horizon_days": 10,
    "engine": {"indicators_registered": 2, "tickers": 35, "warmup_bars": 60},
    "sample": {"tickers": 35, "method": "deterministic-stratified",
               "cadence": "weekly"},
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

PLAN = full_universe.UniversePlan(
    regions={"usa": [f"T{i}" for i in range(20)],
             "sweden": ["S1", "S2", "S3"],
             "germany": ["G1", "G2"],
             "hongkong": [f"HK{i}" for i in range(12)],
             "india": [f"I{i}" for i in range(9)],
             "japan": [f"J{i}" for i in range(5)]},
    order=["usa", "sweden", "germany", "hongkong", "india", "japan"],
    m_unique=51, dropped=["Z-ONLY"], registry_ts="2026-09-13T17:33:25+00:00")
M_UNIQUE = sum(len(v) for v in PLAN.regions.values())
assert M_UNIQUE == PLAN.m_unique == 51        # fixture-registry pin
assert len(PLAN.regions["hongkong"]) > 10 and len(PLAN.order) >= 5


def mk_row(t, overall=71.0):
    return {"ticker": t, "signal": "BUY", "overall": overall, "momentum": 60.0,
            "trend": 65.0, "volatility": 50.0, "volume": 55.0, "close": 10.5}


def mk_frame(bars=10):
    return pd.DataFrame({
        "date": pd.date_range(NOW.date() - timedelta(days=bars), periods=bars),
        "open": [10.0] * bars, "high": [11.0] * bars, "low": [9.0] * bars,
        "close": [10.5] * bars, "volume": [1000.0] * bars})


def scored_by_region(skip=()):
    return {r: ([] if r in skip else [mk_row(t) for t in PLAN.regions[r]])
            for r in PLAN.order}


def scored_out(skip=()):
    results = scored_by_region(skip)
    meta = {t: {"ticker": t, "region": r, "row": mk_row(t),
                "last_bar": FRESH_BAR, "has_usable_store": True, "error": ""}
            for r, rows in results.items() for t in (x["ticker"] for x in rows)}
    return {"results": results, "meta": meta,
            "last_bars": {r: [FRESH_BAR] * len(rows)
                          for r, rows in results.items()},
            "scalars": {"attempted": PLAN.m_unique,
                        "scored_regions": {r: len(v) for r, v in results.items()},
                        "scored_total": sum(len(v) for v in results.values()),
                        "error_count": 0, "errors_shown": [],
                        "errors_truncated": 0}}


def assignments(nf=(), dead=(), thin=(), scored_by=PLAN.order, plan=PLAN):
    skip = set(nf) | set(dead) | set(thin)
    a = {t: store_io.SCORED for r in scored_by for t in plan.regions[r]
         if t not in skip}
    a.update({t: store_io.NOT_FETCHED for t in nf})
    a.update({t: store_io.DEAD_AT_FETCH for t in dead})
    a.update({t: store_io.INSUFFICIENT_ROWS for t in thin})
    return a


def facts_done(plan=PLAN):
    return {t: {"batch_completed": True}
            for r in plan.order for t in plan.regions[r]}


def html(doc=None, results=None, bar=FRESH_BAR, note=None):
    import demo_render
    return demo_render.render(PLAN, scored_by_region() if results is None
                              else results,
                              NOW.strftime("%Y-%m-%d 05:00 UTC"), bar,
                              SAMPLE_DOC if doc is None else doc, note)


# Golden pin (the ONE allowed absolute pin — it IS the f2c02f6 published
# page): rendered 1-decimal Overall of the live page rows at f2c02f6.
GOLDEN_35 = {
    "MSFT": "57.8", "DIS": "45.8", "WMT": "48.8", "NVDA": "52.6",
    "XOM": "58.8", "CSCO": "55.6", "AMZN": "60.7", "MA": "53.6",
    "BAC": "41.9", "JPM": "43.2", "CRM": "40.8", "HD": "38.1",
    "PG": "58.5", "ADBE": "43.3", "JNJ": "48.6", "V": "55.9",
    "AAPL": "51.2", "META": "60.9", "GOOGL": "60.4", "UNH": "53.8",
    "SAAB-B.ST": "64.2", "ATCO-A.ST": "62.3", "SEB-A.ST": "54.3",
    "ASSA-B.ST": "54.9", "SWED-A.ST": "57.5", "ERIC-B.ST": "45.3",
    "SAP.DE": "51.3", "DBK.DE": "39.0", "ALV.DE": "41.0", "SIE.DE": "60.5",
    "DTE.DE": "43.9", "BMW.DE": "39.1", "BAS.DE": "43.9", "VOW3.DE": "45.7",
    "IFX.DE": "62.8"}
assert len(GOLDEN_35) == 35
