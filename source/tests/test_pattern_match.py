"""Tests for Echo Chamber and Match Finder (MC 3841.T2)."""
import numpy as np
import pandas as pd
import pytest

from rocket.technical.models import Signal
from rocket.technical.pattern_match import EchoChamber, MatchFinder, pearson


def _df_from_closes(closes):
    closes = np.asarray(closes, dtype=np.float64)
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.full(len(closes), 1000.0),
    })


# ── pearson helper ────────────────────────────────────────────────
def test_pearson_basics():
    assert pearson(np.array([1., 2, 3]), np.array([2., 4, 6])) == pytest.approx(1.0)
    assert pearson(np.array([1., 2, 3]), np.array([6., 4, 2])) == pytest.approx(-1.0)
    assert pearson(np.array([1., 1, 1]), np.array([1., 2, 3])) == 0.0  # zero variance
    assert pearson(np.array([1.]), np.array([1.])) == 0.0  # too short


# ── Echo Chamber ──────────────────────────────────────────────────
def test_echo_chamber_finds_planted_pattern():
    """A sine pattern planted in history is found with high correlation."""
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 0.05, 400)
    base = 100 + noise
    pattern = 8 * np.sin(np.linspace(0, 2 * np.pi, 20))
    # plant the pattern at [81:101], followed by a rising path [101:121],
    # and make the recent window identical to the planted pattern
    base[81:101] = 100 + pattern
    base[101:121] = 100 + pattern[-1] + np.linspace(1, 10, 20)
    base[-20:] = 100 + pattern
    df = _df_from_closes(base)
    result = EchoChamber(correlation_window=20, lookback=300).calculate(df)
    assert result.values["best_correlation"] > 0.95
    # the winning offset must point at the planted segment, not noise
    assert result.values["best_offset"] == 81


def test_echo_chamber_projection_direction_matches_continuation():
    """The planted continuation rises → projection should drift positive."""
    rng = np.random.default_rng(12)
    base = 100 + rng.normal(0, 0.05, 400)
    pattern = 8 * np.sin(np.linspace(0, 2 * np.pi, 20))
    base[81:101] = 100 + pattern
    base[101:121] = 100 + pattern[-1] + np.linspace(1, 10, 20)
    # make the recent window identical to the planted pattern
    base[-20:] = 100 + pattern
    df = _df_from_closes(base)
    result = EchoChamber(correlation_window=20, lookback=300).calculate(df)
    assert result.values["best_correlation"] > 0.99
    assert result.values["projected_drift"] > 0
    assert result.signal == Signal.BUY


def test_echo_chamber_correlation_floor_forces_hold():
    """Random-walk data (no good match) must HOLD via the correlation floor."""
    rng = np.random.default_rng(13)
    df = _df_from_closes(100 + np.cumsum(rng.normal(0, 2, 400)))
    result = EchoChamber(correlation_window=20, lookback=300,
                         min_correlation=0.99).calculate(df)
    assert result.signal == Signal.HOLD


def test_echo_chamber_self_match_perfect():
    """The most recent window matched against itself gives corr 1.0 only if
    an identical segment exists earlier; a pure ramp has a unique shape, so
    the best match is the adjacent window — still high correlation."""
    df = _df_from_closes(np.linspace(100, 200, 400))
    result = EchoChamber(correlation_window=20, lookback=300).calculate(df)
    assert result.values["best_correlation"] > 0.99


def test_echo_chamber_dilation_sweep_runs():
    df = _df_from_closes(100 + np.sin(np.linspace(0, 20 * np.pi, 400)) * 5)
    result = EchoChamber(enable_dilation=True).calculate(df)
    assert 0.2 <= result.values["dilation"] <= 3.0
    assert result.values["best_correlation"] > 0.9  # periodic data matches well


def test_echo_chamber_insufficient_and_flat_guards():
    r = EchoChamber().calculate(_df_from_closes(np.full(30, 100.0)))
    assert r.signal == Signal.HOLD and r.values["status"] == "insufficient_data"
    r = EchoChamber().calculate(_df_from_closes(np.full(200, 100.0)))
    assert r.values["status"] == "flat_data"


def test_echo_chamber_nan_input_guard():
    """NaN in the RECENT window must force HOLD (no crash, no fire)."""
    closes = np.linspace(100, 150, 200)
    closes[-20:] = np.nan
    r = EchoChamber().calculate(_df_from_closes(closes))
    assert r.signal == Signal.HOLD


# ── Match Finder ──────────────────────────────────────────────────
def test_match_finder_selects_best_reference():
    rng = np.random.default_rng(21)
    recent = 100 + np.sin(np.linspace(0, 2 * np.pi, 20)) * 5
    filler = 100 + rng.normal(0, 0.1, 200)
    rising = np.linspace(recent[-1], recent[-1] + 10, 20)
    refs = {
        # sine reference contains the exact pattern followed by a rise
        "sine": np.concatenate([filler, recent, rising]),
        "ramp": np.linspace(50, 150, 240),
        "noise": 100 + np.cumsum(rng.normal(0, 2, 240)),
    }
    df = _df_from_closes(np.concatenate([filler[:-20], recent]))
    result = MatchFinder(reference_series=refs).calculate(df)
    assert result.values["best_match"] == "sine"
    assert result.values["best_correlation"] > 0.99
    assert result.values["projected_drift"] > 0
    assert result.signal == Signal.BUY


def test_match_finder_floor_and_guards():
    rng = np.random.default_rng(22)
    df = _df_from_closes(100 + np.cumsum(rng.normal(0, 2, 200)))
    refs = {"a": np.linspace(1, 2, 240)}
    r = MatchFinder(reference_series=refs, min_correlation=0.99).calculate(df)
    assert r.signal == Signal.HOLD
    r = MatchFinder(reference_series={}).calculate(df)
    assert r.values["status"] == "insufficient_data"
    r = MatchFinder(reference_series={"short": np.array([1.0, 2.0])}).calculate(df)
    assert r.values["status"] == "no_match"
