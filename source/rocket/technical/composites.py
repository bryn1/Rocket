"""Composite sentiment indicators: Fear & Greed + Chimera (MC 3841.T3).

Headless ports of TheUltimator5's "Fear & Greed" and "Chimera" Pine
scripts. Chart visuals (tables, colors, plots) are not ported.

Deviations from the originals (documented per the DoD):
- Fear & Greed's market-external data (VIX, put/call, junk bonds,
  treasuries, advance/decline) is accepted as optional reference arrays;
  when absent, the component falls back the same way the original does
  (neutral 50) or to the original's own fallback path (breadth).
- TradingView built-ins without documented formulas use the standard
  definitions (Wilder RSI/ATR/DMI, SMA-based Bollinger, %K/%D stochastic).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .base import BaseIndicator, normalize_score
from .models import IndicatorResult, Signal, SignalCategory


def _seed_nan(values: np.ndarray) -> np.ndarray:
    """Replace leading NaNs with the first valid value (smoothing guard)."""
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return values
    return np.where(np.isnan(values), valid[0], values)


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < period or period < 1:
        return out
    c = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1:] = (c[period:] - c[:-period]) / period
    return out


def _rolling_extrema(values: np.ndarray, period: int, fn) -> np.ndarray:
    """NaN-aware rolling min/max (fn is np.nanmax/np.nanmin)."""
    out = np.full(len(values), np.nan)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        valid = window[~np.isnan(window)]
        out[i] = fn(valid) if valid.size else np.nan
    return out


def _cnn_normalize(values: np.ndarray, period: int) -> float:
    """CNN-style z-score normalization: 50 + z*15, clamped to [0, 100]."""
    v = values[-1]
    if np.isnan(v):
        return 50.0
    window = values[-period:]
    mean = np.nanmean(window)
    std = np.nanstd(window)
    if np.isnan(mean) or std == 0 or np.isnan(std):
        return 50.0
    return float(np.clip(50.0 + (v - mean) / std * 15.0, 0.0, 100.0))


def _minmax_scale(values: np.ndarray, period: int) -> float:
    """Scale the last value to 0-100 over a rolling window (original Scale())."""
    v = values[-1]
    if np.isnan(v):
        return 50.0
    window = values[-period:]
    lo, hi = np.nanmin(window), np.nanmax(window)
    if hi - lo == 0:
        return 50.0
    return float(np.clip((v - lo) / (hi - lo) * 100.0, 0.0, 100.0))


def _wilder_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain, avg_loss = gains[:period].mean(), losses[:period].mean()
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _atr(highs, lows, closes, period: int) -> np.ndarray:
    tr = np.maximum(highs - lows, np.maximum(
        np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    return _sma(tr, period)


def _dmi(highs, lows, closes, period: int):
    """Wilder +DI/-DI/ADX (same core as the rube_goldberg module)."""
    from .rube_goldberg import _wilder_di
    return _wilder_di(highs, lows, closes, period)


def _stoch_d(highs, lows, closes, k_period: int, d_period: int) -> np.ndarray:
    n = len(closes)
    k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        hi = highs[i - k_period + 1:i + 1].max()
        lo = lows[i - k_period + 1:i + 1].min()
        k[i] = 100.0 * (closes[i] - lo) / (hi - lo) if hi != lo else 50.0
    return _sma(_seed_nan(k), d_period)


@dataclass
class FearGreed(BaseIndicator):
    """7-component CNN-style Fear & Greed index for a single symbol."""
    norm_period: int = 252
    scale_period: int = 100
    momentum_ma: int = 125
    rsi_period: int = 14
    vix: np.ndarray = field(default=None, repr=False)
    put_call: np.ndarray = field(default=None, repr=False)
    junk_bond_yield: np.ndarray = field(default=None, repr=False)
    treasury_yield: np.ndarray = field(default=None, repr=False)
    benchmark_closes: dict = field(default_factory=dict)
    min_data_points: int = 260

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        if len(df) < self.min_data_points:
            return self._empty("insufficient_data")
        closes = df['close'].to_numpy(dtype=np.float64)
        highs = df['high'].to_numpy(dtype=np.float64)
        lows = df['low'].to_numpy(dtype=np.float64)

        # 1. Market momentum: close vs its 125-day SMA, CNN-normalized.
        ma = _sma(closes, self.momentum_ma)
        momentum_raw = np.where(ma == 0, 0.0, (closes / ma - 1.0) * 100.0)
        momentum = _cnn_normalize(momentum_raw, self.norm_period)

        # 2. Stock price strength: RSI min-max scaled over the norm period.
        rsi = _wilder_rsi(closes, self.rsi_period)
        strength = _minmax_scale(rsi, self.norm_period)

        # 3. Breadth: McClellan-style via benchmark 5d-return comparison
        #    (the original's own fallback when ADV/DEC data is absent).
        breadth = 50.0
        if self.benchmark_closes:
            main_ret = closes[-1] / closes[-6] - 1.0 if closes[-6] else 0.0
            outperforming, total = 0, 0
            for ref in self.benchmark_closes.values():
                ref = np.asarray(ref, dtype=np.float64)
                if len(ref) >= 6:
                    total += 1
                    if ref[-1] / ref[-6] - 1.0 > main_ret:
                        outperforming += 1
            if total:
                breadth = float(np.clip((outperforming / total) * 100.0, 0, 100))

        # 4. Put/call: inverted CNN normalization of the 5-day SMA.
        pcr = 50.0
        if self.put_call is not None and len(self.put_call) >= 5:
            pcr_arr = np.asarray(self.put_call, dtype=np.float64)
            pcr = _cnn_normalize(-_sma(pcr_arr, 5), self.norm_period)

        # 5. Volatility: VIX relative to its 50-day SMA, inverted.
        vol = 50.0
        if self.vix is not None and len(self.vix) >= 50:
            vix = np.asarray(self.vix, dtype=np.float64)
            vix_ma = _sma(vix, 50)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio_series = np.where(vix_ma == 0, 1.0, vix / vix_ma)
            vol = _cnn_normalize(1.0 - ratio_series, self.norm_period)

        # 6. Safe haven: stock momentum vs bond change, min-max scaled.
        stock_ret = _sma(closes - np.roll(closes, 20), 20)
        bond_change = 0.0
        if self.treasury_yield is not None and len(self.treasury_yield) >= 21:
            ty = np.asarray(self.treasury_yield, dtype=np.float64)
            bond_change = ty[-1] - ty[-21]
        safe_haven = _minmax_scale(np.array([stock_ret[-1] - bond_change]), self.scale_period)

        # 7. Junk bond demand: yield-spread deviation from its 5-day SMA.
        junk = 50.0
        if self.junk_bond_yield is not None and self.treasury_yield is not None \
                and len(self.junk_bond_yield) >= 6:
            jy = np.asarray(self.junk_bond_yield, dtype=np.float64)
            ty = np.asarray(self.treasury_yield, dtype=np.float64)
            spread = (jy - ty) - _sma(jy - ty, 5)
            junk = _minmax_scale(spread, self.scale_period)

        components = {
            "market_momentum": momentum, "stock_strength": strength,
            "breadth": breadth, "put_call": pcr, "volatility": vol,
            "safe_haven": safe_haven, "junk_bond": junk,
        }
        overall = float(np.mean(list(components.values())))

        signal, score = Signal.HOLD, 0.0
        if overall <= 25:
            signal, score = Signal.BUY, normalize_score((25.0 - overall) / 25.0)
        elif overall >= 75:
            signal, score = Signal.SELL, -normalize_score((overall - 75.0) / 25.0)
        return IndicatorResult(
            name="FearGreed", score=score, signal=signal,
            category=SignalCategory.MOMENTUM,
            values={**components, "overall": overall, "status": "ok"},
        )

    def _empty(self, status: str) -> IndicatorResult:
        return IndicatorResult(
            name="FearGreed", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.MOMENTUM,
            values={"overall": 50.0, "status": status},
        )


@dataclass
class Chimera(BaseIndicator):
    """5-component z-normalized sentiment oscillator with SMA cross logic."""
    lookback_period: int = 252
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_length: int = 20
    bb_mult: float = 2.0
    stoch_k: int = 14
    stoch_d: int = 3
    atr_period: int = 14
    short_smoothing: int = 1
    long_smoothing: int = 12
    min_data_points: int = 260

    def _raw_series(self, closes, highs, lows):
        """Per-bar composite of the five z-normalized components."""
        lb = self.lookback_period
        n = len(closes)

        # 1. RSI z-normalized per bar.
        rsi = _seed_nan(_wilder_rsi(closes, self.rsi_period))
        rsi_ma = _sma(rsi, lb)
        rsi_std = pd.Series(rsi).rolling(lb).std().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            rsi_n = np.where((rsi_std == 0) | np.isnan(rsi_std), 50.0,
                             np.clip(50.0 + (rsi - rsi_ma) / rsi_std * 15.0, 0, 100))

        # 2. MACD oscillator z-normalized per bar.
        ema_fast = pd.Series(closes).ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = pd.Series(closes).ewm(span=self.macd_slow, adjust=False).mean()
        macd_osc = (ema_fast - ema_slow
                    - (ema_fast - ema_slow).ewm(span=self.macd_signal, adjust=False).mean()).to_numpy()
        macd_ma = _sma(macd_osc, lb)
        macd_std = pd.Series(macd_osc).rolling(lb).std().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            macd_n = np.where((macd_std == 0) | np.isnan(macd_std), 50.0,
                              np.clip(50.0 + (macd_osc - macd_ma) / macd_std * 15.0, 0, 100))

        # 3. Bollinger Band position z-normalized per bar.
        basis = _sma(closes, self.bb_length)
        dev = self.bb_mult * pd.Series(closes).rolling(self.bb_length).std().to_numpy()
        upper, lower = basis + dev, basis - dev
        with np.errstate(divide="ignore", invalid="ignore"):
            bb_pos = np.where((upper - lower) == 0, 50.0,
                              100.0 * (closes - lower) / (upper - lower))
        bb_pos = _seed_nan(bb_pos)
        bb_ma = _sma(bb_pos, lb)
        bb_std = pd.Series(bb_pos).rolling(lb).std().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            bb_n = np.where((bb_std == 0) | np.isnan(bb_std), 50.0,
                            np.clip(50.0 + (bb_pos - bb_ma) / bb_std * 15.0, 0, 100))

        # 4. Stochastic %D (raw 0-100).
        stoch = _stoch_d(highs, lows, closes, self.stoch_k, self.stoch_d)
        stoch_n = np.where(np.isnan(stoch), 50.0, stoch)

        # 5. Directional volatility: DI difference amplified by relative ATR.
        plus_di, minus_di, _ = _dmi(highs, lows, closes, self.atr_period)
        di_diff = plus_di - minus_di
        atr = _atr(highs, lows, closes, self.atr_period)
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = np.where(closes == 0, 0.0, atr / closes * 100.0)
        atr_pct = _seed_nan(atr_pct)
        atr_ma = _sma(atr_pct, lb)
        vol_factor = np.where(atr_ma == 0, 1.0, atr_pct / atr_ma)
        dv = di_diff * vol_factor  # NaN during warm-up, like Pine's na
        dv_hi = _rolling_extrema(dv, lb, np.nanmax)
        dv_lo = _rolling_extrema(dv, lb, np.nanmin)
        half = (dv_hi - dv_lo) / 2.0
        mid = (dv_hi + dv_lo) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_n = np.where((half == 0) | np.isnan(half), 50.0,
                             np.clip(50.0 + 50.0 * (dv - mid) / half, 0, 100))

        return (rsi_n + macd_n + bb_n + stoch_n + atr_n) / 5.0, {
            "rsi": float(rsi_n[-1]), "macd": float(macd_n[-1]),
            "bollinger": float(bb_n[-1]), "stochastic": float(stoch_n[-1]),
            "directional_volatility": float(atr_n[-1]),
        }

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        if len(df) < self.min_data_points:
            return self._empty("insufficient_data")
        closes = df['close'].to_numpy(dtype=np.float64)
        highs = df['high'].to_numpy(dtype=np.float64)
        lows = df['low'].to_numpy(dtype=np.float64)

        raw, components = self._raw_series(closes, highs, lows)
        short_ma = _sma(raw, max(1, self.short_smoothing))
        long_ma = _sma(raw, max(1, self.long_smoothing))
        short_val, long_val = float(short_ma[-1]), float(long_ma[-1])

        # Original trend state: bullish after a short-over-long crossover,
        # bearish after a crossunder, persisting between crossovers.
        is_bullish = True
        for i in range(1, len(raw)):
            if np.isnan(short_ma[i]) or np.isnan(long_ma[i]):
                continue
            if short_ma[i] > long_ma[i] and short_ma[i - 1] <= long_ma[i - 1]:
                is_bullish = True
            elif short_ma[i] < long_ma[i] and short_ma[i - 1] >= long_ma[i - 1]:
                is_bullish = False

        signal, score = Signal.HOLD, 0.0
        if is_bullish and short_val > long_val:
            signal, score = Signal.BUY, normalize_score((short_val - 50.0) / 50.0)
        elif not is_bullish and short_val < long_val:
            signal, score = Signal.SELL, -normalize_score((50.0 - short_val) / 50.0)
        return IndicatorResult(
            name="Chimera", score=score, signal=signal,
            category=SignalCategory.MOMENTUM,
            values={**components, "combined": float(raw[-1]),
                    "short_smoothing": short_val, "long_smoothing": long_val,
                    "is_bullish_trend": is_bullish, "status": "ok"},
        )

    def _empty(self, status: str) -> IndicatorResult:
        return IndicatorResult(
            name="Chimera", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.MOMENTUM,
            values={"combined": 50.0, "status": status},
        )
