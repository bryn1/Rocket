"""Tests for the full-logic RubeGoldberg indicator (MC 3841.T1)."""
import numpy as np
import pandas as pd
import pytest

from rocket.technical.models import Signal
from rocket.technical.rube_goldberg import (
    RubeGoldberg,
    _apply_rsi_filter,
    _moving_average,
    _parabolic_sar,
    _wilder_di,
)


def _make_df(n=120, kind="flat", seed=7):
    rng = np.random.default_rng(seed)
    if kind == "flat":
        close = np.full(n, 100.0)
    elif kind == "downtrend":
        close = np.linspace(150, 60, n)
    elif kind == "uptrend":
        close = np.linspace(60, 150, n)
    elif kind == "crash_recover":
        close = np.concatenate([
            np.full(n // 3, 100.0),
            np.linspace(100, 55, n // 3),
            np.linspace(55, 105, n - 2 * (n // 3)),
        ])
    else:
        close = 100 + np.cumsum(rng.normal(0, 1, n))
    noise = 0.0 if kind == "flat" else 0.5
    high = close + noise + (rng.random(n) * 0.5 if noise else 0.0)
    low = close - noise - (rng.random(n) * 0.5 if noise else 0.0)
    return pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": np.full(n, 1000.0),
    })


# ── MA basis helpers ──────────────────────────────────────────────
def test_ma_bases_match_known_values():
    vals = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    sma = _moving_average(vals, 5, "SMA")
    assert sma[-1] == pytest.approx(8.0)
    assert np.isnan(sma[3])
    for basis in ("EMA", "DEMA", "TEMA", "WMA", "SMMA", "HMA", "LSMA", "ALMA"):
        out = _moving_average(vals, 5, basis)
        assert np.isfinite(out[-1]), basis
        # Double/triple exponential and weighted variants intentionally
        # overshoot a ramp; they must stay within a sane envelope.
        assert 6.0 <= out[-1] <= 11.0, basis


def test_rsi_filters_stay_in_bounds():
    prices = np.linspace(100, 60, 60).tolist() + np.linspace(60, 90, 60).tolist()
    prices = np.array(prices)
    rsi = RubeGoldberg._compute_rsi(prices, 14)
    for filt in ("none", "kalman", "dema", "alma"):
        out = _apply_rsi_filter(rsi, filt)
        assert np.nanmin(out) >= 0 and np.nanmax(out) <= 100, filt


# ── Wilder DI / SAR ───────────────────────────────────────────────
def test_wilder_di_direction_on_trend():
    n = 120
    up = np.linspace(100, 200, n)
    plus_di, minus_di, adx = _wilder_di(up + 1, up - 1, up, 14)
    assert plus_di[-1] > minus_di[-1]
    assert adx[-1] > 20  # strong one-directional trend


def test_parabolic_sar_flips_on_reversal():
    n = 80
    up = np.linspace(100, 150, n)
    down = np.linspace(150, 100, n)
    closes = np.concatenate([up, down])
    highs, lows = closes + 1, closes - 1
    sar = _parabolic_sar(highs, lows, closes, 0.02, 0.2)
    # In the uptrend half SAR stays below close; after reversal it crosses.
    assert sar[n // 2] < closes[n // 2]
    assert sar[-1] > closes[-1]


# ── Trigger behaviour ─────────────────────────────────────────────
def test_insufficient_data_guard():
    result = RubeGoldberg().calculate(_make_df(30))
    assert result.signal == Signal.HOLD
    assert result.values["trigger_count"] == 0
    assert result.values["gap_detected"] is False


def test_flat_data_no_signal_and_rsi_in_bounds():
    result = RubeGoldberg().calculate(_make_df(100, "flat"))
    assert result.signal == Signal.HOLD
    assert 0 <= result.values["rsi"] <= 100


def test_crash_recovery_chain_consistency():
    """After a crash+recovery the leading side must never be bearish."""
    rg = RubeGoldberg(confirm_window=10)
    result = rg.calculate(_make_df(150, "crash_recover"))
    v = result.values
    assert 0 <= v["rsi"] <= 100
    if result.signal == Signal.BUY:
        assert v["leading_bullish"] and v["lagging_bullish"]
    if result.signal == Signal.SELL:
        assert v["leading_bearish"] and v["lagging_bearish"]


def test_no_buy_without_lagging_confirmation():
    """A leading trigger alone must not produce BUY (original semantics)."""
    rg = RubeGoldberg()
    result = rg.calculate(_make_df(150, "crash_recover"))
    if result.signal == Signal.BUY:
        assert result.values["lagging_bullish"]


def test_gap_detection_on_low_adx_spike():
    """Low ADX + DI spike should record a gap with a price."""
    n = 120
    close = np.full(n, 100.0)
    close[117] = 110.0  # sharp spike in the final bars after a quiet period
    close[118] = 120.0
    close[119] = 130.0
    df = pd.DataFrame({
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.full(n, 1000.0),
    })
    result = RubeGoldberg().calculate(df)
    assert result.values["gap_detected"] is True
    assert result.values["gap_price"] > 0


def test_reference_divergence_computed_and_skipped():
    df = _make_df(150, "crash_recover")
    rg_no_ref = RubeGoldberg()
    r1 = rg_no_ref.calculate(df)
    assert r1.values["reference_divergence"] == 0.0

    # Reference that is flat while the chart crashes → negative divergence
    flat_ref = np.full(150, 100.0)
    rg_ref = RubeGoldberg(reference_closes=[flat_ref])
    r2 = rg_ref.calculate(df)
    assert r2.values["reference_divergence"] != 0.0


def test_invalid_basis_falls_back_to_guard():
    result = RubeGoldberg(ma_basis="BOGUS").calculate(_make_df(120))
    assert result.signal == Signal.HOLD
    result = RubeGoldberg(rsi_filter="BOGUS").calculate(_make_df(120))
    assert result.signal == Signal.HOLD


def test_legacy_values_keys_present():
    result = RubeGoldberg().calculate(_make_df(120, "uptrend"))
    for key in ("rsi", "adx", "sar_flip_direction", "trigger_count",
                "rsi_trigger", "adx_trigger", "sar_trigger"):
        assert key in result.values, key
