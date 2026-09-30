"""Pattern-matching indicators: Echo Chamber (MC 3841.T2).

Echo Chamber finds the historical window that best Pearson-correlates with
the most recent bars, then projects the path that followed the historical
match forward, scaled to the current price range. Ported from
TheUltimator5's "Echo Chamber" Pine script; chart visuals are not ported —
rocket is a headless scanner.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .base import BaseIndicator, normalize_score
from .models import IndicatorResult, Signal, SignalCategory


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two equal-length arrays (NaN-safe)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    da, db = a - a.mean(), b - b.mean()
    den = np.sqrt((da * da).sum() * (db * db).sum())
    if den == 0:
        return 0.0
    return float((da * db).sum() / den)


def _project(hist_segment: np.ndarray, following: np.ndarray,
             recent: np.ndarray) -> list:
    """Rescale the path that followed the historical match to today's price.

    The projection is anchored at the current close and scaled by the ratio
    of the recent window's range to the matched segment's range.
    """
    hist_range = hist_segment.max() - hist_segment.min()
    recent_range = recent.max() - recent.min()
    scale = (recent_range / hist_range) if hist_range != 0 else 1.0
    anchor = float(recent[-1])
    if len(following) == 0:
        return []
    base = float(following[0])
    return [anchor + (float(p) - base) * scale for p in following]


@dataclass
class EchoChamber(BaseIndicator):
    """Best-correlation historical match + forward projection."""
    correlation_window: int = 20
    lookback: int = 300
    projection_length: int = 20
    enable_dilation: bool = False
    min_correlation: float = 0.8
    min_data_points: int = 60

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        if len(df) < self.min_data_points:
            return self._empty("insufficient_data")

        closes = df['close'].to_numpy(dtype=np.float64)
        w, proj_len = self.correlation_window, self.projection_length
        if w < 2 or len(closes) < w + 2:
            return self._empty("insufficient_data")

        recent = closes[-w:]
        recent_range = recent.max() - recent.min()
        if recent_range == 0:
            return self._empty("flat_data")

        # Dilation factors to sweep (1.0 only unless enabled).
        dilations = np.arange(0.2, 3.01, 0.1) if self.enable_dilation else np.array([1.0])

        best_corr, best_start, best_dilation = -2.0, None, 1.0
        # A candidate start s means: matched segment = closes[s:s+w] and the
        # bars that FOLLOWED it in history are closes[s+w : s+w+proj_span].
        for dil in dilations:
            sif = max(1.0 / dil, 0.001)
            proj_span = int(np.ceil(sif * proj_len))
            end = min(self.lookback + w, len(closes) - w - proj_span)
            for s in range(0, max(end, 0)):
                idx = s + np.round(np.arange(w) * sif).astype(int)
                hist = closes[idx]
                if np.isnan(hist).any() or hist.max() - hist.min() == 0:
                    continue
                corr = pearson(recent, hist)
                if corr > best_corr:
                    best_corr, best_start, best_dilation = corr, s, float(dil)

        if best_start is None:
            return self._empty("no_match")

        sif = max(1.0 / best_dilation, 0.001)
        proj_span = int(np.ceil(sif * proj_len))
        idx = best_start + np.round(np.arange(w) * sif).astype(int)
        hist = closes[idx]
        fidx = best_start + w - 1 + np.round(np.arange(1, proj_span + 1) * sif).astype(int)
        fidx = fidx[fidx < len(closes)]
        following = closes[fidx]
        projected = _project(hist, following, recent)

        signal, score = Signal.HOLD, 0.0
        if best_corr >= self.min_correlation and projected:
            drift = projected[-1] - projected[0]
            if drift > 0:
                signal, score = Signal.BUY, normalize_score(best_corr)
            elif drift < 0:
                signal, score = Signal.SELL, normalize_score(-best_corr)
        return IndicatorResult(
            name="EchoChamber", score=score, signal=signal,
            category=SignalCategory.TREND,
            values={
                "best_correlation": best_corr,
                "best_offset": int(best_start),
                "dilation": best_dilation,
                "projected_path": [round(p, 6) for p in projected],
                "projected_drift": (projected[-1] - projected[0]) if projected else 0.0,
                "status": "ok",
            },
        )

    def _empty(self, status: str) -> IndicatorResult:
        return IndicatorResult(
            name="EchoChamber", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.TREND,
            values={"best_correlation": 0.0, "best_offset": None, "dilation": 1.0,
                    "projected_path": [], "projected_drift": 0.0, "status": status},
        )


@dataclass
class MatchFinder(BaseIndicator):
    """Best Pearson match of the recent window against reference series."""
    correlation_window: int = 20
    projection_length: int = 20
    min_correlation: float = 0.8
    reference_series: dict = field(default_factory=dict)
    min_data_points: int = 60

    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        df = self._normalize_columns(df)
        if len(df) < self.min_data_points or not self.reference_series:
            return self._empty("insufficient_data")

        closes = df['close'].to_numpy(dtype=np.float64)
        w = self.correlation_window
        recent = closes[-w:]
        if recent.max() - recent.min() == 0:
            return self._empty("flat_data")

        best_name, best_corr, best_start = None, -2.0, None
        for name, ref in self.reference_series.items():
            ref = np.asarray(ref, dtype=np.float64)
            # Slide the recent window across the reference's history; the
            # bars that followed the winning window are the projection.
            end = len(ref) - w  # allow matching the reference's final window
            for s in range(0, max(end, 0)):
                seg = ref[s:s + w]
                if np.isnan(seg).any() or seg.max() - seg.min() == 0:
                    continue
                corr = pearson(recent, seg)
                if corr > best_corr:
                    best_name, best_corr, best_start = name, corr, s

        if best_name is None:
            return self._empty("no_match")

        ref = np.asarray(self.reference_series[best_name], dtype=np.float64)
        following = ref[best_start + w:best_start + w + self.projection_length]
        projected = _project(ref[best_start:best_start + w], following, recent)

        signal, score = Signal.HOLD, 0.0
        if best_corr >= self.min_correlation and projected:
            drift = projected[-1] - projected[0]
            if drift > 0:
                signal, score = Signal.BUY, normalize_score(best_corr)
            elif drift < 0:
                signal, score = Signal.SELL, normalize_score(-best_corr)
        return IndicatorResult(
            name="MatchFinder", score=score, signal=signal,
            category=SignalCategory.TREND,
            values={
                "best_match": best_name,
                "best_correlation": best_corr,
                "projected_path": [round(p, 6) for p in projected],
                "projected_drift": (projected[-1] - projected[0]) if projected else 0.0,
                "status": "ok",
            },
        )

    def _empty(self, status: str) -> IndicatorResult:
        return IndicatorResult(
            name="MatchFinder", signal=Signal.HOLD, score=0.0,
            category=SignalCategory.TREND,
            values={"best_match": None, "best_correlation": 0.0,
                    "projected_path": [], "projected_drift": 0.0, "status": status},
        )
