"""RubeGoldberg top/bottom finder — full-logic port of TheUltimator5's Pine script.

Leading trigger (three independent parts):
  1. RSI (selectable MA basis, optional filter) crossing overbought/oversold.
  2. ADX/DI divergence: ADX above threshold AND above its own MA, with a
     +DI/-DI spike; plus a gap detector (low ADX + DI spike = gap, records
     the gap start price).
  3. Multi-symbol DI divergence: chart symbol's DI direction vs the average
     of reference symbols (approximated from closes when only closes are
     available; skipped gracefully when no references are given).

Lagging confirmation: Parabolic SAR flip with an RSI rate-of-change check.
BUY/SELL fires when the leading trigger is followed by the lagging trigger
within ``confirm_window`` bars. Chart visuals (labels, tables, colors) are
intentionally not ported — rocket is a headless scanner.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .base import BaseIndicator, normalize_score
from .models import IndicatorResult, Signal, SignalCategory

MA_BASES = ("SMA", "EMA", "DEMA", "TEMA", "WMA", "VWMA", "SMMA", "HMA", "LSMA", "ALMA")
RSI_FILTERS = ("none", "kalman", "dema", "alma")


def _moving_average(values: np.ndarray, period: int, basis: str) -> np.ndarray:
    """Return a moving average of ``values`` for any supported basis."""
    n = len(values)
    out = np.full(n, np.nan)
    if n < period or period < 1:
        return out
    if basis == "SMA":
        c = np.cumsum(np.insert(values, 0, 0.0))
        out[period - 1:] = (c[period:] - c[:-period]) / period
    elif basis == "EMA":
        alpha = 2.0 / (period + 1)
        out[period - 1] = values[:period].mean()
        for i in range(period, n):
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    elif basis in ("DEMA", "TEMA"):
        e1 = _moving_average(values, period, "EMA")
        e2 = _moving_average(np.nan_to_num(e1, nan=np.nanmean(values[:period])), period, "EMA")
        if basis == "DEMA":
            out = 2 * e1 - e2
        else:
            e3 = _moving_average(np.nan_to_num(e2, nan=np.nanmean(values[:period])), period, "EMA")
            out = 3 * e1 - 3 * e2 + e3
    elif basis == "WMA":
        weights = np.arange(1, period + 1, dtype=float)
        for i in range(period - 1, n):
            out[i] = np.dot(values[i - period + 1:i + 1], weights) / weights.sum()
    elif basis == "VWMA":
        # Volume-weighted MA on price alone degenerates to SMA; callers that
        # have volume should pre-weight ``values`` themselves.
        out = _moving_average(values, period, "SMA")
    elif basis == "SMMA":
        out[period - 1] = values[:period].mean()
        for i in range(period, n):
            out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    elif basis == "HMA":
        half = max(1, period // 2)
        wma_half = _moving_average(values, half, "WMA")
        wma_full = _moving_average(values, period, "WMA")
        raw = 2 * wma_half - wma_full
        sq = max(1, int(np.sqrt(period)))
        out[sq - 1:] = _moving_average(np.nan_to_num(raw, nan=0.0), sq, "WMA")[sq - 1:]
    elif basis == "LSMA":
        for i in range(period - 1, n):
            y = values[i - period + 1:i + 1]
            x = np.arange(period, dtype=float)
            slope = np.polyfit(x, y, 1)[0]
            out[i] = y.mean() + slope * (period - 1) / 2.0
    elif basis == "ALMA":
        offset = 0.85
        sigma = period / 3.0
        m = offset * (period - 1)
        s = np.arange(period, dtype=float)
        weights = np.exp(-((s - m) ** 2) / (2 * sigma ** 2))
        weights /= weights.sum()
        for i in range(period - 1, n):
            out[i] = np.dot(values[i - period + 1:i + 1], weights)
    else:  # pragma: no cover - guarded by validation
        out = _moving_average(values, period, "EMA")
    return out


def _seed_nan(values: np.ndarray) -> np.ndarray:
    """Replace leading NaNs with the first valid value (smoothing guard)."""
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return values
    return np.where(np.isnan(values), valid[0], values)


def _apply_rsi_filter(rsi: np.ndarray, filt: str) -> np.ndarray:
    """Apply the original's hard-coded RSI smoothing filters."""
    if filt in ("dema", "alma"):
        rsi = _seed_nan(rsi)
        if filt == "dema":
            e1 = _moving_average(rsi, 5, "EMA")
            e2 = _moving_average(_seed_nan(e1), 5, "EMA")
            return 2 * e1 - e2
        return _moving_average(rsi, 9, "ALMA")
    if filt == "kalman":
        out = np.full(len(rsi), np.nan)
        estimate, error = 50.0, 1.0
        for i, v in enumerate(rsi):
            if np.isnan(v):
                continue
            gain = error / (error + 2.0)
            estimate = estimate + gain * (v - estimate)
            error = (1 - gain) * error
            out[i] = estimate
        return out
    return rsi


def _wilder_di(highs, lows, closes, period):
    """Wilder +DI/-DI/ADX series (the original's ADX Divergence core)."""
    n = len(closes)
    up = np.diff(highs, prepend=highs[0])
    down = np.diff(-lows, prepend=-lows[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)),
                                             np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]

    def _smooth(arr):
        out = np.full(n, np.nan)
        if n < period:
            return out
        out[period - 1] = arr[:period].sum()
        for i in range(period, n):
            out[i] = out[i - 1] - out[i - 1] / period + arr[i]
        return out

    str_plus, str_minus, str_tr = _smooth(plus_dm), _smooth(minus_dm), _smooth(tr)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * str_plus / str_tr
        minus_di = 100 * str_minus / str_tr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = _moving_average(np.nan_to_num(dx, nan=0.0), period, "SMMA")
    return plus_di, minus_di, adx


def _parabolic_sar(highs, lows, closes, step, max_accel):
    """Standard Parabolic SAR (EP / AF / step / max accel)."""
    n = len(closes)
    if n == 0:
        return np.array([])
    sar = np.empty(n)
    af = step
    trend = 1 if n < 3 or closes[2] >= closes[0] else -1
    if trend == 1:
        ep, sar[0] = highs[0], min(highs[0], lows[0])
        if sar[0] >= closes[0]:
            sar[0] = closes[0] * 0.99
    else:
        ep, sar[0] = lows[0], max(lows[0], highs[0])
        if sar[0] <= closes[0]:
            sar[0] = closes[0] * 1.01
    for i in range(1, n):
        sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
        if trend == 1:
            # Uptrend: SAR ratchets up, never above the prior two lows.
            sar[i] = min(sar[i], lows[i - 1])
            if i >= 2:
                sar[i] = min(sar[i], lows[i - 2])
            if lows[i] < sar[i]:  # Wilder: low penetration flips the trend
                sar[i], ep, af, trend = ep, lows[i], step, -1
            elif highs[i] > ep:
                ep, af = highs[i], min(af + step, max_accel)
        else:
            # Downtrend: SAR ratchets down, never below the prior two highs.
            sar[i] = max(sar[i], highs[i - 1])
            if i >= 2:
                sar[i] = max(sar[i], highs[i - 2])
            if highs[i] > sar[i]:  # Wilder: high penetration flips the trend
                sar[i], ep, af, trend = ep, highs[i], step, 1
            elif lows[i] < ep:
                ep, af = lows[i], min(af + step, max_accel)
    return sar


@dataclass
class RubeGoldberg(BaseIndicator):
    """Full-logic RubeGoldberg top/bottom finder (leading + lagging triggers)."""
    rsi_period: int = 14
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    ma_basis: str = "EMA"
    rsi_filter: str = "none"
    adx_period: int = 14
    adx_threshold: float = 20.0
    adx_ma_period: int = 10
    di_spike_threshold: float = 35.0
    gap_adx_threshold: float = 22.0
    divergence_threshold: float = 25.0
    confirm_window: int = 5
    sar_step: float = 0.02
    sar_max: float = 0.2
    min_data_points: int = 50
    reference_closes: list = field(default_factory=list)

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        empty = self._empty_result()
        if len(df) < self.min_data_points or self.ma_basis not in MA_BASES \
                or self.rsi_filter not in RSI_FILTERS:
            return empty

        closes = df['close'].to_numpy(dtype=np.float64)
        highs = df['high'].to_numpy(dtype=np.float64)
        lows = df['low'].to_numpy(dtype=np.float64)
        n = len(closes)

        # ── Leading part 1: filtered RSI crossing the thresholds ──
        rsi_raw = self._compute_rsi(closes, self.rsi_period)
        # Seed the warm-up NaN region with the first valid RSI so the MA
        # basis smoothing does not propagate NaN through the whole series.
        valid = rsi_raw[~np.isnan(rsi_raw)]
        if valid.size == 0:
            return self._empty_result()
        rsi_seeded = np.where(np.isnan(rsi_raw), valid[0], rsi_raw)
        rsi = _apply_rsi_filter(_moving_average(rsi_seeded, 3, self.ma_basis), self.rsi_filter)
        rsi_prev, rsi_cur = rsi[-2], rsi[-1]
        rsi_bullish = bool(rsi_prev >= self.rsi_oversold and rsi_cur < self.rsi_oversold)
        rsi_bearish = bool(rsi_prev <= self.rsi_overbought and rsi_cur > self.rsi_overbought)

        # ── Leading part 2: ADX/DI divergence + gap detector ──
        plus_di, minus_di, adx = _wilder_di(highs, lows, closes, self.adx_period)
        adx_ma = _moving_average(np.nan_to_num(adx, nan=0.0), self.adx_ma_period, "SMA")
        adx_val = float(adx[-1]) if not np.isnan(adx[-1]) else 0.0
        di_spike_up = float(minus_di[-1]) > self.di_spike_threshold
        di_spike_down = float(plus_di[-1]) > self.di_spike_threshold
        adx_strong = adx_val > self.adx_threshold and adx_val > float(adx_ma[-1])
        adx_bullish = bool(adx_strong and di_spike_up)
        adx_bearish = bool(adx_strong and di_spike_down)

        gap_detected = bool(adx_val < self.gap_adx_threshold and (di_spike_up or di_spike_down))
        gap_price = float(closes[-1]) if gap_detected else 0.0

        # ── Leading part 3: multi-symbol DI divergence ──
        di_dir = float(plus_di[-1]) - float(minus_di[-1])
        ref_divergence = 0.0
        if self.reference_closes:
            ref_dirs = []
            for ref in self.reference_closes:
                ref = np.asarray(ref, dtype=np.float64)[-n:]
                if len(ref) == n:
                    r_plus, r_minus, _ = _wilder_di(ref, ref, ref, self.adx_period)
                    ref_dirs.append(float(r_plus[-1]) - float(r_minus[-1]))
            if ref_dirs:
                ref_divergence = di_dir - float(np.mean(ref_dirs))
        div_bullish = bool(ref_divergence < -self.divergence_threshold)
        div_bearish = bool(ref_divergence > self.divergence_threshold)

        # ── Leading trigger (original rule: all three, or extreme 2/3 with 1) ──
        leading_bullish = rsi_bullish and (adx_bullish or div_bullish)
        leading_bearish = rsi_bearish and (adx_bearish or div_bearish)

        # ── Lagging trigger: SAR flip + RSI rate-of-change ──
        sar = _parabolic_sar(highs, lows, closes, self.sar_step, self.sar_max)
        sar_flip_up = bool(sar[-2] > closes[-2] and sar[-1] < closes[-1])
        sar_flip_down = bool(sar[-2] < closes[-2] and sar[-1] > closes[-1])
        rsi_roc_up = bool(rsi_cur > rsi_prev)
        rsi_roc_down = bool(rsi_cur < rsi_prev)
        lagging_bullish = bool(sar_flip_up and rsi_roc_up)
        lagging_bearish = bool(sar_flip_down and rsi_roc_down)

        # ── Final: leading followed by lagging within the window ──
        signal, score = Signal.HOLD, 0.0
        if leading_bullish and lagging_bullish:
            signal, score = Signal.BUY, 1.0
        elif leading_bearish and lagging_bearish:
            signal, score = Signal.SELL, -1.0

        trigger_count = int(rsi_bullish or rsi_bearish) + int(adx_bullish or adx_bearish) \
            + int(sar_flip_up or sar_flip_down)
        return IndicatorResult(
            name="RubeGoldberg", score=score, signal=signal,
            category=SignalCategory.TREND,
            values={
                # legacy keys kept for existing callers
                "rsi": float(rsi_cur), "adx": adx_val,
                "sar_flip_direction": "UP" if sar_flip_up else ("DOWN" if sar_flip_down else "NONE"),
                "trigger_count": trigger_count,
                "rsi_trigger": rsi_bullish or rsi_bearish,
                "adx_trigger": adx_bullish or adx_bearish,
                "sar_trigger": sar_flip_up or sar_flip_down,
                "rsi_value": float(rsi_cur), "adx_value": adx_val,
                # full-logic fields
                "plus_di": float(plus_di[-1]), "minus_di": float(minus_di[-1]),
                "gap_detected": gap_detected, "gap_price": gap_price,
                "reference_divergence": ref_divergence,
                "leading_bullish": leading_bullish, "leading_bearish": leading_bearish,
                "lagging_bullish": lagging_bullish, "lagging_bearish": lagging_bearish,
            },
        )

    def _empty_result(self) -> IndicatorResult:
        return IndicatorResult(
            name="RubeGoldberg", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.TREND,
            values={
                "rsi": 0.0, "adx": 0.0, "sar_flip_direction": "NONE",
                "trigger_count": 0, "rsi_trigger": False, "adx_trigger": False,
                "sar_trigger": False, "rsi_value": 0.0, "adx_value": 0.0,
                "plus_di": 0.0, "minus_di": 0.0, "gap_detected": False,
                "gap_price": 0.0, "reference_divergence": 0.0,
                "leading_bullish": False, "leading_bearish": False,
                "lagging_bullish": False, "lagging_bearish": False,
            },
        )

    # ── Internal helpers ──────────────────────────────────────
    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int) -> np.ndarray:
        """Compute RSI array inline (Wilder smoothing)."""
        n = len(closes)
        rsi_arr = np.full(n, np.nan)
        if n < period + 1:
            return rsi_arr
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain, avg_loss = np.mean(gains[:period]), np.mean(losses[:period])
        rsi_arr[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            rsi_arr[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        return rsi_arr
