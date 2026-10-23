from abc import ABC, abstractmethod
import math

import pandas as pd
from .models import IndicatorResult


def normalize_score(score: float) -> float:
    """Clamp a raw score to [-1, 1]. NaN input is neutral 0.0, never max:
    NaN slips through both max and min, so the bare clamp turned a corrupt
    indicator score into a full-strength bullish +1.0 (MC 11185 / F1)."""
    value = float(score)
    if math.isnan(value):
        return 0.0
    return max(-1.0, min(1.0, value))


class BaseIndicator(ABC):
    """Abstract base class for all technical indicators."""

    category_name = ""

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to lowercase OHLCV standard."""
        col_map = {}
        for col in df.columns:
            lower = col.lower()
            if lower in ('close', 'high', 'low', 'open', 'volume', 'adj_close'):
                col_map[col] = lower
        if col_map:
            df = df.rename(columns=col_map)
        return df

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> IndicatorResult:
        """Calculate indicator on OHLCV DataFrame. Return IndicatorResult."""
        ...

    def _last(self, series: pd.Series):
        """Safely get the last value of a Series."""
        val = series.iloc[-1] if len(series) > 0 else 0.0
        return float(val)

    def _prev(self, series: pd.Series, n: int = 1):
        """Safely get a previous value."""
        if len(series) <= n:
            return None
        return float(series.iloc[-(n + 1)])
