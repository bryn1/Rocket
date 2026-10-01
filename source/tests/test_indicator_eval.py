"""Tests for rocket.backtest.indicator_eval (MC 3874 T2).

Synthetic frames only, no network, no DB. Small indicator subsets keep the
full-suite contribution modest; the CLI test exercises the real 34-module
registry on two tiny tickers.
"""
import json

import numpy as np
import pandas as pd
import pytest

from rocket.backtest.indicator_eval import (_evidence, main, run_indicator_eval,
                                            to_indicator_frame)
from rocket.technical.base import BaseIndicator
from rocket.technical.models import IndicatorResult, Signal, SignalCategory
from rocket.technical.momentum import RSI
from rocket.technical.patterns import (
    AutoFractal, CupAndHandle, DoubleTopBottom, HeadShoulders,
    PatternDetectorCombined, WedgePattern,
)

PATTERN_CLASSES = [DoubleTopBottom, HeadShoulders, WedgePattern, AutoFractal,
                   CupAndHandle, PatternDetectorCombined]
TOP_KEYS = ["schema_version", "generated_at", "data_last_bar", "engine",
            "universe", "costs_pct", "horizons_days", "primary_horizon_days",
            "entry_rule", "incomplete_dropped", "indicators"]
IND_KEYS = ["name", "category", "risk_only", "bars_evaluated", "calc_errors",
            "n_signals", "n_buy", "n_sell", "horizons", "evidence_weight",
            "evidence_t"]
BLOCK_KEYS = ["n", "hit_rate", "avg_gross_pct", "avg_net_pct",
              "median_net_pct", "best_pct", "worst_pct"]


def make_frame(n=110, opens=None, closes=None):
    """Deterministic synthetic OHLCV in the lowercase cache shape, with a
    date column so bars carry dates."""
    base = np.linspace(100.0, 110.0, n)
    wig = base + 2 * np.sin(np.arange(n) / 3.0)
    close = wig if closes is None else np.asarray(closes, dtype=float)
    open_ = wig - 0.5 if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": open_,
        "high": np.maximum(open_, close) + 1.0,
        "low": np.minimum(open_, close) - 1.0,
        "close": close,
        "volume": np.linspace(1e6, 2e6, n),
    })


class ScriptIndicator(BaseIndicator):
    """Stub indicator: a pinned signal per bar number; optional error bars."""

    def __init__(self, script=None, raises=False):
        self.script = dict(script or {})
        self.raises = raises

    def calculate(self, df):
        t = len(df) - 1
        if self.raises:
            raise ValueError("planted indicator failure")
        return IndicatorResult(name="Script", score=0.0,
                               signal=self.script.get(t, Signal.HOLD),
                               category=SignalCategory.MOMENTUM)


def test_to_indicator_frame_fixes_pattern_indicators():
    low = make_frame()
    conv = to_indicator_frame(low)
    assert {"Open", "High", "Low", "Close", "Volume"} <= set(conv.columns)
    assert "date" in conv.columns  # non-OHLCV columns pass through
    assert list(to_indicator_frame(conv).columns) == list(conv.columns)  # idempotent
    for cls in PATTERN_CLASSES:
        with pytest.raises(KeyError):  # the verified V4 failure on lowercase
            cls().calculate(low)
        assert cls().calculate(conv).signal in list(Signal)


def test_determinism_identical_except_generated_at():
    frames = {"AAA": make_frame(), "BBB": make_frame(closes=np.linspace(90, 110, 110))}
    universe = {"usa": ["AAA", "BBB"]}
    a = run_indicator_eval(frames, universe, indicators=[RSI()])
    b = run_indicator_eval(frames, universe, indicators=[RSI()])
    ja = json.dumps(a)
    jb = json.dumps(b)
    assert ja.replace(a["generated_at"], "@") == jb.replace(b["generated_at"], "@")


def test_schema_fields_order_rounding_and_nulls():
    doc = run_indicator_eval({"AAA": make_frame()}, {"usa": ["AAA"]},
                             indicators=[RSI(), ScriptIndicator()])
    assert list(doc) == TOP_KEYS
    assert doc["schema_version"] == 1
    assert doc["engine"] == {"indicators_registered": 2, "tickers": 1,
                             "warmup_bars": 60}
    assert doc["costs_pct"] == {"commission": 0.1, "slippage": 0.05, "roundtrip": 0.3}
    assert doc["horizons_days"] == [5, 10, 20] and doc["primary_horizon_days"] == 10
    assert doc["data_last_bar"] == str(make_frame()["date"].iloc[-1])[:10]
    assert list(doc["indicators"]) == ["RSI", "ScriptIndicator"]  # alphabetical
    si = doc["indicators"]["ScriptIndicator"]
    assert list(si) == IND_KEYS
    assert si["name"] == "Script" and si["category"] == "momentum"
    assert si["risk_only"] is False and si["n_signals"] == 0
    assert si["evidence_weight"] == "insufficient" and si["evidence_t"] is None
    for side_block in si["horizons"].values():
        assert list(side_block) == ["long", "short"]
        blk = side_block["long"]
        assert list(blk) == BLOCK_KEYS and blk["n"] == 0
        assert all(blk[k] is None for k in BLOCK_KEYS[1:])  # nulls when n == 0
    rsi = doc["indicators"]["RSI"]
    assert list(rsi["horizons"]) == ["5", "10", "20"]
    for side_block in rsi["horizons"].values():
        for blk in side_block.values():
            if blk["n"]:
                assert blk["hit_rate"] == round(blk["hit_rate"], 4)
                for k in BLOCK_KEYS[2:]:
                    assert blk[k] == round(blk[k], 4)


def test_entry_uses_next_open_exit_uses_close():
    # Signal on bar 65; the OPEN of the entry bar (66) jumps to 200 while
    # closes stay at 100 — a lookahead entry (close of 65, or close of 66)
    # would give 0%, an open-exit at bar 70 (open 150) would give -25%.
    n = 90
    opens = np.full(n, 100.0)
    opens[66] = 200.0
    opens[70] = 150.0
    doc = run_indicator_eval(
        {"AAA": make_frame(n=n, opens=opens, closes=np.full(n, 100.0))},
        {"usa": ["AAA"]}, indicators=[ScriptIndicator({65: Signal.BUY})])
    blk = doc["indicators"]["ScriptIndicator"]["horizons"]["5"]["long"]
    assert blk["n"] == 1
    assert blk["avg_gross_pct"] == -50.0   # (100 - 200) / 200 * 100
    assert blk["avg_net_pct"] == -50.3     # net = gross - 0.3 roundtrip


def test_edge_trigger_one_trade_and_opposite_exit():
    n = 100
    closes = np.full(n, 100.0)
    closes[70:] = 105.0
    script = {t: Signal.BUY for t in range(65, 70)}
    script[70] = Signal.SELL  # first opposite bar; persists only one bar
    doc = run_indicator_eval(
        {"AAA": make_frame(n=n, opens=np.full(n, 100.0), closes=closes)},
        {"usa": ["AAA"]}, indicators=[ScriptIndicator(script)])
    si = doc["indicators"]["ScriptIndicator"]
    # BUY state persists bars 65-69 yet yields ONE long entry, not five.
    assert (si["n_buy"], si["n_sell"], si["n_signals"]) == (1, 1, 2)
    assert doc["incomplete_dropped"] == {"long": 0, "short": 0}
    for h in ("5", "10", "20"):
        lb = si["horizons"][h]["long"]
        # entry open(66)=100, exit at close(70)=105 (before horizon end)
        assert (lb["n"], lb["avg_gross_pct"], lb["avg_net_pct"]) == (1, 5.0, 4.7)
        sb = si["horizons"][h]["short"]
        # short: entry open(71)=100, no later BUY, exit close(70+h)=105
        assert (sb["n"], sb["avg_gross_pct"], sb["avg_net_pct"]) == (1, -5.0, -5.3)


def test_incomplete_trades_dropped_and_counted():
    doc = run_indicator_eval(
        {"AAA": make_frame(n=80)}, {"usa": ["AAA"]},
        indicators=[ScriptIndicator({78: Signal.BUY})])
    si = doc["indicators"]["ScriptIndicator"]
    assert si["n_buy"] == 1  # transition counted
    assert all(si["horizons"][h]["long"]["n"] == 0 for h in ("5", "10", "20"))
    assert doc["incomplete_dropped"] == {"long": 3, "short": 0}  # per horizon


def test_calc_errors_counted_and_run_continues():
    frames = {"AAA": make_frame(), "BBB": make_frame(closes=np.linspace(80, 120, 110))}
    doc = run_indicator_eval(frames, {"usa": ["AAA", "BBB"]},
                             indicators=[RSI(), ScriptIndicator(raises=True)])
    si = doc["indicators"]["ScriptIndicator"]
    assert si["calc_errors"] == 2 * (110 - 60)  # every (ticker, bar) replay
    assert si["n_signals"] == 0  # errored bars treated as HOLD
    assert si["evidence_weight"] == "error"
    assert si["horizons"]["5"]["long"]["n"] == 0
    assert "RSI" in doc["indicators"]  # partial failure never aborts the run
    # Total failure (every indicator erroring) raises instead (§C1).
    with pytest.raises(RuntimeError):
        run_indicator_eval(frames, {"usa": ["AAA", "BBB"]},
                           indicators=[ScriptIndicator(raises=True)])


def test_bars_evaluated_is_mean_frame_length_minus_warmup():
    # F1 (TEST-verdict MC 3874): §3 defines bars_evaluated as the mean of
    # max(0, len(df) - 60) across tickers. The bug: prepared.values() yields
    # (frame, opens, closes) TUPLES, so len() measured 3 -> 0 every run.
    single = run_indicator_eval({"AAA": make_frame(n=100)}, {"usa": ["AAA"]},
                                indicators=[ScriptIndicator()])
    assert single["indicators"]["ScriptIndicator"]["bars_evaluated"] == 40
    two = run_indicator_eval({"AAA": make_frame(n=100), "BBB": make_frame(n=130)},
                             {"usa": ["AAA", "BBB"]},
                             indicators=[ScriptIndicator()])
    assert two["indicators"]["ScriptIndicator"]["bars_evaluated"] == 55  # (40+70)/2


def test_evidence_error_threshold_scales_with_bars_evaluated():
    # F1 side effect: with bars_evaluated == 0 the §3 threshold
    # calc_errors > 0.5 * bars_evaluated * tickers collapsed to "> 0", so ONE
    # errored bar marked an indicator "error". On the correct 100-bar value
    # (40, one ticker) the threshold is 0.5*40*1 = 20: 1 errored bar stays
    # "insufficient" (n == 0 nets), 21 errored bars flip it to "error".
    assert _evidence([], calc_errors=1, bars_evaluated=40, tickers=1)[0] == "insufficient"
    assert _evidence([], calc_errors=21, bars_evaluated=40, tickers=1)[0] == "error"


def test_cli_writes_schema_json(tmp_path):
    cache = tmp_path / "raw"
    cache.mkdir()
    for stem in ("AAA", "BBB"):
        make_frame(n=80).to_csv(cache / f"{stem}.csv", index=False)
    out = tmp_path / "stats.json"
    assert main(["--cache-dir", str(cache), "--tickers", "BBB,AAA",
                 "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["schema_version"] == 1
    assert len(doc["indicators"]) == 34  # the real INDICATORS registry
    assert doc["universe"] == {"cli": ["AAA", "BBB"]}  # stems, sorted
    assert doc["data_last_bar"] == str(make_frame(n=80)["date"].iloc[-1])[:10]
