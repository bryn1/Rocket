"""Tests for the unFair Value Gap Detector (MC 3841.T4)."""
import numpy as np
import pandas as pd
import pytest

from rocket.technical.models import Signal, SignalCategory
from rocket.technical.ufvg import UnFairValueGapDetector


def _df_from(opens, highs, lows, closes):
    n = len(closes)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 1000.0),
    })


def _flat_series(n=120, price=100.0, jitter=0.05, seed=7):
    """Quiet consolidation: tiny ranges, no trend."""
    rng = np.random.default_rng(seed)
    close = price + rng.normal(0, jitter, n)
    high = close + 0.1
    low = close - 0.1
    open_ = close + rng.normal(0, 0.02, n)
    return open_, high, low, close


def test_flat_market_consolidates_no_gap():
    o, h, l, c = _flat_series()
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["status"] == "ok"
    assert r.values["is_consolidating"] is True
    assert r.values["gap_fill_detected"] is False
    assert r.signal == Signal.HOLD


def test_di_spike_after_low_adx_triggers_gap_fill():
    """Quiet market, then a violent one-directional burst on the last bar."""
    o, h, l, c = _flat_series(120)
    # last bar: huge range, close far above open (bullish DI spike)
    c[-1] = c[-2] + 8.0
    o[-1] = c[-2] + 0.05
    h[-1] = c[-1] + 0.2
    l[-1] = o[-1] - 0.1
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["gap_fill_detected"] is True
    # bullish DI spike → gap fill is a mean-reversion SELL
    assert r.signal == Signal.SELL
    assert r.score < 0


def test_bearish_spike_buys():
    o, h, l, c = _flat_series(120)
    c[-1] = c[-2] - 8.0
    o[-1] = c[-2] - 0.05
    l[-1] = c[-1] - 0.2
    h[-1] = o[-1] + 0.1
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["gap_fill_detected"] is True
    assert r.signal == Signal.BUY
    assert r.score > 0


def test_spike_with_strong_adx_does_not_fire():
    """A DI spike while ADX is already strong is NOT a gap fill."""
    n = 120
    # strong sustained uptrend → high ADX
    close = 100 + np.linspace(0, 40, n) + np.random.default_rng(3).normal(0, 0.3, n)
    high, low = close + 0.6, close - 0.6
    open_ = close - 0.1
    # add a burst on the last bar
    c = close.copy()
    c[-1] = c[-2] + 5.0
    h = high.copy(); h[-1] = c[-1] + 0.3
    l = low.copy(); l[-1] = c[-2] - 0.2
    o = open_.copy(); o[-1] = c[-2] + 0.1
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["gap_fill_detected"] is False
    assert r.signal == Signal.HOLD


def test_gap_line_breaks_when_price_touches_level():
    """After a gap fill, a later bar touching the gap level breaks the line."""
    o, h, l, c = _flat_series(120)
    # gap fill at bar 100
    c[100] = c[99] + 8.0
    o[100] = c[99] + 0.05
    h[100] = c[100] + 0.2
    l[100] = o[100] - 0.1
    # quiet again, then a bar whose range spans the gap level
    level = o[100]
    c[110] = level + 2.0
    o[110] = level - 2.0
    h[110] = level + 2.5
    l[110] = level - 2.5
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["gap_line_active"] is False
    assert r.values["gap_price_level"] == pytest.approx(level)


def test_gap_line_stays_active_when_untouched():
    o, h, l, c = _flat_series(120)
    c[100] = c[99] + 8.0
    o[100] = c[99] + 0.05
    h[100] = c[100] + 0.2
    l[100] = o[100] - 0.1
    # price stays at the elevated level afterwards, never returning to the gap
    c[101:] = c[100] + 0.05
    h[101:] = c[101:] + 0.1
    l[101:] = c[101:] - 0.1
    o[101:] = c[101:] - 0.02
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.values["gap_line_active"] is True
    assert r.values["gap_price_level"] == pytest.approx(o[100])


def test_insufficient_data():
    o, h, l, c = _flat_series(30)
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    assert r.signal == Signal.HOLD
    assert r.values["status"] == "insufficient_data"


def test_values_keys_present_on_success():
    o, h, l, c = _flat_series()
    r = UnFairValueGapDetector().calculate(_df_from(o, h, l, c))
    for key in ("adx", "di_plus", "di_minus", "gap_fill_detected",
                "gap_line_active", "gap_price_level", "is_consolidating"):
        assert key in r.values
    assert r.category == SignalCategory.VOLATILITY
