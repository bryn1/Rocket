"""MC #11198 — pins: the deleted risk-multiplier reads stay dead.

`compute_rocket_score` used to feed `compute_risk` from two reads that could
never fire: ATR was read from `r.score`, which ATR emits as literal 0 (the
real values sit in `values`), and the volume read looked for name
"VWAPIndicator" + key "volume_ratio" while VWAP emits name "VWAP" with keys
{vwap, price}. The deletion is score-neutral — byte-equality across the 35
frozen golden fixtures on BOTH scorer paths was proven pre/post (see the
MC #11198 evidence) — this file pins the deadness itself:

* the committed `indicator_stats.json`: ATR is risk-only with n_signals == 0;
* the exact dead interface: ATR's `score` is 0 and VWAP's name is "VWAP"
  with no `volume_ratio` key;
* the multiplier read is unreachable: a max-ATR input (huge score AND huge
  atr values) and a volume_ratio-carrying VWAP result leave the scored
  channels byte-identical, with `atr_pct` pinned at 0.0.

Runs ONLY on the committed frozen fixture store (source/tests/data/golden35);
no network, no source/data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
for _p in (str(TESTS), str(TESTS.parent / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io                                          # noqa: E402
import full_universe                                     # noqa: E402
import rocket.scoring.rocket_score as rs                 # noqa: E402
from demo_page_fixtures import GOLDEN35_DIR, GOLDEN_35   # noqa: E402
from rocket.data.models import TickerInfo, Region        # noqa: E402
from rocket.technical.volatility import ATR              # noqa: E402
from rocket.technical.volume import VWAPIndicator        # noqa: E402

REPO_ROOT = TESTS.parent.parent                # committed indicator_stats.json
TICKER = "AAPL"
assert TICKER in GOLDEN_35, "golden-35 fixture must stay committed"


def score_rocket(df):
    return rs.compute_rocket_score(
        df,
        TickerInfo(ticker=TICKER, name=TICKER, region=Region.US),
        current_price=float(df["close"].iloc[-1]),
        social_sentiment=False,
    )


def scored_channel(result) -> str:
    """The risk-relevant output slice, as full-precision repr bytes."""
    return repr((result["rocket_score"], result["risk_result"],
                 result["rocket_signal"]))


def _inject(instance, mutate):
    """Wrap one indicator instance's calculate() to mutate its result."""
    orig = instance.calculate

    def patched(df):
        r = orig(df)
        mutate(r)
        return r

    return patched


# ── deadness evidence (a): the committed stats artifact ─────────────────────

def test_committed_stats_atr_never_signaled():
    stats = json.loads(
        (REPO_ROOT / "indicator_stats.json").read_text(encoding="utf-8"))
    atr = stats["indicators"]["ATR"]
    assert atr["risk_only"] is True
    assert atr["bars_evaluated"] > 0, "stats must show ATR really was evaluated"
    assert atr["n_signals"] == 0 and atr["n_buy"] == 0 and atr["n_sell"] == 0


# ── deadness evidence (b): the exact interface the deleted reads used ───────

def test_atr_score_is_literal_zero_and_vwap_name_key_mismatch():
    df = store_io.read_cache(GOLDEN35_DIR / full_universe.cache_filename(TICKER))
    a = ATR().calculate(df)
    assert a.name == "ATR" and a.score == 0, "ATR's score channel must stay 0"
    assert "atr" in a.values and "atr_pct" in a.values
    v = VWAPIndicator().calculate(df)
    assert v.name == "VWAP", "VWAP must not emit the name the read looked for"
    assert "volume_ratio" not in v.values


# ── the deleted read is unreachable: extreme inputs move nothing ────────────

def test_max_atr_input_leaves_score_byte_identical(monkeypatch):
    df = store_io.read_cache(GOLDEN35_DIR / full_universe.cache_filename(TICKER))
    base = scored_channel(score_rocket(df))

    inst = next(i for i in rs.INDICATORS if isinstance(i, ATR))
    monkeypatch.setattr(inst, "calculate", _inject(inst, lambda r: r.__dict__.update(
        score=50.0, values={**r.values, "atr": 1e6, "atr_pct": 1e6})))
    moved = score_rocket(df)
    assert scored_channel(moved) == base, "a screaming ATR moved the score"
    assert moved["risk_result"].atr_pct == 0.0
    # squeeze-only reachable range, computed exactly as 1 - 0.5*(0.3*{0.9,0.4,0.1})
    assert 0.865 - 1e-12 <= moved["risk_result"].risk_multiplier <= 0.985 + 1e-12


def test_vwap_volume_ratio_cannot_reach_risk(monkeypatch):
    df = store_io.read_cache(GOLDEN35_DIR / full_universe.cache_filename(TICKER))
    base = scored_channel(score_rocket(df))

    inst = next(i for i in rs.INDICATORS if isinstance(i, VWAPIndicator))
    monkeypatch.setattr(inst, "calculate", _inject(inst, lambda r: r.__dict__.update(
        name="VWAPIndicator", values={**r.values, "volume_ratio": 1000.0})))
    moved = score_rocket(df)
    assert scored_channel(moved) == base, "an injected volume_ratio moved the score"
    assert moved["risk_result"].volume_anomaly == 1.0
