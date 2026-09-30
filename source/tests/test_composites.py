"""Tests for Fear & Greed and Chimera composites (MC 3841.T3)."""
import numpy as np
import pandas as pd
import pytest

from rocket.technical.models import Signal
from rocket.technical.composites import FearGreed, Chimera, _cnn_normalize, _minmax_scale


def _make_df(n=300, kind="trend_up", seed=5):
    rng = np.random.default_rng(seed)
    if kind == "trend_up":
        close = 100 + np.linspace(0, 50, n) + rng.normal(0, 0.5, n)
    elif kind == "trend_down":
        close = 150 - np.linspace(0, 50, n) + rng.normal(0, 0.5, n)
    elif kind == "flat":
        close = np.full(n, 100.0)
    else:  # cycle
        close = 100 + np.sin(np.linspace(0, 8 * np.pi, n)) * 10
    high, low = close + 0.5, close - 0.5
    return pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": np.full(n, 1000.0),
    })


# ── shared normalization helpers ──────────────────────────────────
def test_cnn_normalize_bounds_and_neutral():
    vals = np.linspace(0, 100, 300)
    # z*15 compression: the max of a uniform ramp lands at ~50 + 1.73*15
    assert _cnn_normalize(vals, 252) == pytest.approx(75.88, abs=0.5)
    assert _cnn_normalize(np.array([50.0, 50.0, 50.0]), 3) == 50.0  # zero std
    assert _cnn_normalize(np.array([np.nan]), 3) == 50.0


def test_minmax_scale_bounds():
    vals = np.linspace(10, 20, 101)
    assert _minmax_scale(vals, 100) == pytest.approx(100.0)
    assert _minmax_scale(np.array([5.0, 5.0]), 2) == 50.0


# ── Fear & Greed ──────────────────────────────────────────────────
def test_fear_greed_score_bounds_and_status():
    result = FearGreed().calculate(_make_df(300, "cycle"))
    v = result.values
    assert v["status"] == "ok"
    for key in ("market_momentum", "stock_strength", "breadth", "put_call",
                "volatility", "safe_haven", "junk_bond"):
        assert 0.0 <= v[key] <= 100.0, key
    assert 0.0 <= v["overall"] <= 100.0


def _df_from(closes):
    closes = np.asarray(closes, dtype=np.float64)
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.full(len(closes), 1000.0),
    })


def test_fear_greed_momentum_component_direction():
    """A sharp rally into the last bars lifts momentum; a sharp drop cuts it.

    (Linear ramps are degenerate here: close/sma125 stays flat when the
    slope is constant, so the test uses step-shaped data.)
    """
    n = 300
    flat = np.full(n, 100.0)
    rally = np.concatenate([flat[:250], 100.0 * 1.03 ** np.arange(50)])
    drop = np.concatenate([flat[:250], 100.0 * 0.97 ** np.arange(50)])
    up = FearGreed().calculate(_df_from(rally)).values["market_momentum"]
    down = FearGreed().calculate(_df_from(drop)).values["market_momentum"]
    assert up > 60
    assert down < 40
    assert up > down


def test_fear_greed_direction_consistency():
    """Never SELL in a crash, never BUY in a rally (conservative bands)."""
    crash = FearGreed().calculate(_make_df(300, "trend_down"))
    rally = FearGreed().calculate(_make_df(300, "trend_up"))
    assert crash.signal != Signal.SELL
    assert rally.signal != Signal.BUY
    # external fear data (rising VIX/PCR, widening junk spread) must push
    # the crash overall lower than the baseline
    fearful = FearGreed(
        vix=np.linspace(10, 80, 300),
        put_call=np.linspace(0.5, 3.0, 300),
        junk_bond_yield=np.linspace(5, 12, 300),
        treasury_yield=np.full(300, 3.0),
        benchmark_closes={"ref": np.linspace(300, 40, 300)},
    ).calculate(_make_df(300, "trend_down"))
    assert fearful.values["overall"] < crash.values["overall"]


def test_fear_greed_flat_is_hold():
    result = FearGreed().calculate(_make_df(300, "flat"))
    assert result.signal == Signal.HOLD


def test_fear_greed_breadth_with_benchmarks():
    """Benchmarks outperforming a crashing main lift the breadth component."""
    df = _make_df(300, "trend_down")
    strong_ref = np.linspace(100, 200, 300)
    r_with = FearGreed(benchmark_closes={"ref": strong_ref}).calculate(df)
    r_without = FearGreed().calculate(df)
    assert r_with.values["breadth"] == 100.0
    assert r_without.values["breadth"] == 50.0


def test_fear_greed_external_series_change_scores():
    df = _make_df(300, "cycle")
    base = FearGreed().calculate(df).values
    vix_high = np.linspace(10, 60, 300)  # rising VIX → fear → higher vol score
    r = FearGreed(vix=vix_high).calculate(df)
    assert r.values["volatility"] > base["volatility"]
    pcr_low = np.full(300, 0.5)  # low put/call = greed
    r2 = FearGreed(put_call=pcr_low).calculate(df)
    assert 0 <= r2.values["put_call"] <= 100


def test_fear_greed_insufficient_data():
    r = FearGreed().calculate(_make_df(100))
    assert r.signal == Signal.HOLD and r.values["status"] == "insufficient_data"


# ── Chimera ───────────────────────────────────────────────────────
def test_chimera_components_in_bounds():
    result = Chimera().calculate(_make_df(300, "cycle"))
    v = result.values
    assert v["status"] == "ok"
    for key in ("rsi", "macd", "bollinger", "stochastic", "directional_volatility"):
        assert 0.0 <= v[key] <= 100.0, key
    assert 0.0 <= v["combined"] <= 100.0


def test_chimera_trend_reversal_cross():
    """A long decline followed by a rally must end bullish-trend (600 bars
    so the 252-bar lookback at the end covers the rally phase)."""
    n = 600
    close = np.concatenate([np.linspace(200, 100, n // 2),
                            np.linspace(100, 190, n // 2)])
    df = pd.DataFrame({
        "open": close, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.full(n, 1000.0),
    })
    result = Chimera().calculate(df)
    assert result.values["is_bullish_trend"] is True
    assert result.signal == Signal.BUY


def test_chimera_downtrend_bearish():
    result = Chimera().calculate(_make_df(600, "trend_down"))
    assert result.values["is_bullish_trend"] is False
    assert result.signal == Signal.SELL


def test_chimera_insufficient_data():
    r = Chimera().calculate(_make_df(100))
    assert r.signal == Signal.HOLD and r.values["status"] == "insufficient_data"


def test_chimera_flat_no_strong_signal():
    result = Chimera().calculate(_make_df(300, "flat"))
    assert abs(result.score) < 0.5 or result.signal == Signal.HOLD
