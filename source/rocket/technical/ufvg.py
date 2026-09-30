"""unFair Value Gap Detector (MC 3841.T4).

Headless port of TheUltimator5's "unFair Value Gap Detector" Pine script.
The original draws gap lines on the chart across five higher timeframes
(request.security); a headless scanner has no chart and no HTF context,
so this port implements the CURRENT-timeframe detection only and reports
the gap state as values. Deviations are documented per the DoD.

Core logic (hard-coded constants in the original, kept here as defaults):
- Wilder DI+/DI-/ADX (adx_length=14)
- gap fill detected when ADX[1] < adx_low_threshold (14) AND a DI spike
  (di_plus_change or di_minus_change > min_jump_val=10)
- the gap level is the OPEN of the detection bar; the line stays valid
  until price touches or crosses it
- consolidating = weak ADX, DI spread < 5, both DI < 25
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import BaseIndicator, normalize_score
from .models import IndicatorResult, Signal, SignalCategory
from .rube_goldberg import _wilder_di


@dataclass
class UnFairValueGapDetector(BaseIndicator):
    """Detects unFair Value Gap fills: low-ADX DI spikes and gap-line state."""
    adx_length: int = 14
    adx_low_threshold: float = 14.0
    min_jump_val: float = 10.0
    consolidating_di_spread: float = 5.0
    consolidating_di_max: float = 25.0
    min_data_points: int = 60

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        if len(df) < self.min_data_points:
            return self._empty("insufficient_data")
        opens = df['open'].to_numpy(dtype=np.float64)
        highs = df['high'].to_numpy(dtype=np.float64)
        lows = df['low'].to_numpy(dtype=np.float64)
        closes = df['close'].to_numpy(dtype=np.float64)

        plus_di, minus_di, adx = _wilder_di(highs, lows, closes, self.adx_length)
        di_plus_change = np.diff(plus_di, prepend=np.nan)
        di_minus_change = np.diff(minus_di, prepend=np.nan)

        # Per-bar gap-fill detection (original: adx[1] < threshold and a DI
        # change spike above min_jump_val).
        adx_prev = np.roll(adx, 1)
        adx_prev[0] = np.nan
        with np.errstate(invalid="ignore"):
            has_di_spike = (di_plus_change > self.min_jump_val) | \
                           (di_minus_change > self.min_jump_val)
            gap_fill = (adx_prev < self.adx_low_threshold) & has_di_spike

        # Gap-line tracking: level = open of the first detection bar of a
        # run; broken when price touches or crosses it afterwards.
        gap_level = np.nan
        gap_start = -1
        line_broken = False
        active_gap = False
        for i in range(len(closes)):
            if gap_fill[i] and not (gap_fill[i - 1] if i > 0 else False):
                gap_level = opens[i]
                gap_start = i
                line_broken = False
                active_gap = True
            if active_gap and i > gap_start:
                touches = (lows[i] <= gap_level + 1e-9) and (highs[i] >= gap_level - 1e-9)
                crosses = (closes[i - 1] - gap_level) * (opens[i] - gap_level) < 0
                if touches or crosses:
                    line_broken = True

        consolidating = bool(
            not np.isnan(adx[-1]) and adx[-1] < self.adx_low_threshold
            and not np.isnan(plus_di[-2]) and not np.isnan(minus_di[-2])
            and abs(plus_di[-2] - minus_di[-2]) < self.consolidating_di_spread
            and plus_di[-1] < self.consolidating_di_max
            and minus_di[-1] < self.consolidating_di_max
        )
        gap_fill_now = bool(gap_fill[-1])
        gap_line_active = active_gap and not line_broken

        # Signal: a fresh gap fill after consolidation is a mean-reversion
        # entry — direction from the DI spike side.
        signal, score = Signal.HOLD, 0.0
        if gap_fill_now:
            spike_up = di_plus_change[-1] > self.min_jump_val
            spike_down = di_minus_change[-1] > self.min_jump_val
            if spike_up and not spike_down:
                signal, score = Signal.SELL, -normalize_score(0.5)
            elif spike_down and not spike_up:
                signal, score = Signal.BUY, normalize_score(0.5)
        return IndicatorResult(
            name="UnFairValueGap", score=score, signal=signal,
            category=SignalCategory.VOLATILITY,
            values={
                "adx": float(adx[-1]) if not np.isnan(adx[-1]) else None,
                "di_plus": float(plus_di[-1]) if not np.isnan(plus_di[-1]) else None,
                "di_minus": float(minus_di[-1]) if not np.isnan(minus_di[-1]) else None,
                "gap_fill_detected": gap_fill_now,
                "gap_line_active": gap_line_active,
                "gap_price_level": float(gap_level) if not np.isnan(gap_level) else None,
                "is_consolidating": consolidating,
                "status": "ok",
            },
        )

    def _empty(self, status: str) -> IndicatorResult:
        return IndicatorResult(
            name="UnFairValueGap", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.VOLATILITY,
            values={"status": status},
        )
