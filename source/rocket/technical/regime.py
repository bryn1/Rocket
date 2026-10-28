"""Market regime detection — classify market as BULL, BEAR, or CHOP.

Uses regional index data (SPY, OMXSPI, SHCOMP, NIFTY) to determine
the broader market context. This affects all signals via a multiplier.

Architecture:
  - Fetch index data via yfinance
  - Compute trend (EMA50 vs EMA200) + volatility (ADX)
  - Classify: BULL / BEAR / CHOP
  - Return multiplier: 1.0 (bull), 0.7 (bear), 0.5 (chop)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    CHOP = "CHOP"


@dataclass
class RegimeResult:
    """Result of regime detection."""
    regime: Regime
    score: float  # 0.0 → 1.0 (confidence in regime classification)
    multiplier: float  # applied to final_score (1.0=bull, 0.7=bear, 0.5=chop)
    index_trend: str = ""  # brief description of index condition
    factors: list[str] = field(default_factory=list)


# Regional index mapping. D1 (MC 11545, B0): this map IS the whole coverage —
# the four regions above get real regime state from the nightly index CSVs
# (scripts/fetch_indices.py); the registry's 11 uncovered regions have no
# index, and index_for_region below is the ONE resolution point: an unmapped
# region resolves to None — NEVER a "^GSPC" default — so callers pass
# index_df=None and the insufficient-data guard below keeps the honest
# CHOP × 0.5 fallback (named + tested in tests/test_fetch_indices.py).
# D5 (MC 11579, owner ruling 2026-10-28) proxy-semantics law: sweden reads
# ^OMXSPI, the OMX Stockholm ALL-SHARE — a whole-exchange proxy for the
# Swedish market regime, NOT the top-30 blue-chip index this map once named
# (Yahoo serves that symbol quote-only — 1 bar, no daily history; root-cause
# probe .audits/202610272245-wirebuild/out/, MC 11571). The broad All-Share
# is the only Yahoo-served full Swedish daily series, so the multiplier
# answers "what is the Swedish MARKET doing": disclosed per D5, not smuggled.
REGION_INDEX = {
    "usa": "^GSPC",        # S&P 500
    "sweden": "^OMXSPI",   # OMX Stockholm All-Share (whole-exchange proxy)
    "china": "000001.SS",  # Shanghai Composite
    "india": "^NSEI",      # NIFTY 50
}


def index_for_region(region: str) -> str | None:
    """Region -> its index ticker, or None when the region has no mapping
    (the D1 honest fallback: None -> no index_df -> CHOP × 0.5 stands)."""
    return REGION_INDEX.get(region)


def detect_regime(
    index_df: pd.DataFrame,
    index_name: str = "^GSPC",
) -> RegimeResult:
    """Detect market regime from index data.

    BULL: index above EMA50 AND EMA200, ADX > 20
    BEAR: index below EMA50, OR VIX > 30 (if available)
    CHOP: ADX < 15 (no clear trend in any timeframe)

    Args:
        index_df: Index OHLCV DataFrame (must have Close column)
        index_name: yfinance ticker for the index

    Returns:
        RegimeResult with regime classification and multiplier
    """
    if index_df is None or index_df.empty or len(index_df) < 50:
        return RegimeResult(
            regime=Regime.CHOP,
            score=0.0,
            multiplier=0.5,
            index_trend="insufficient data",
            factors=["insufficient data"],
        )

    # Compute indicators
    close = index_df["Close"]
    ema50 = close.rolling(window=50).mean()
    ema200 = close.rolling(window=200).mean()
    adx = _compute_adx(index_df)

    # Price position
    current_price = close.iloc[-1]
    price_vs_ema50 = current_price / float(ema50.iloc[-1]) - 1.0 if float(ema50.iloc[-1]) > 0 else 0.0
    price_vs_ema200 = current_price / float(ema200.iloc[-1]) - 1.0 if float(ema200.iloc[-1]) > 0 else 0.0

    # Trend score: how far above/below EMAs
    trend_score = (price_vs_ema50 + price_vs_ema200) / 2.0

    factors = [
        f"price vs EMA50:{price_vs_ema50:+.2%}",
        f"price vs EMA200:{price_vs_ema200:+.2%}",
        f"ADX:{adx:.1f}",
    ]

    # Classification
    if adx < 15:
        regime = Regime.CHOP
        multiplier = 0.5
        score = 0.6  # moderate confidence in chop
        index_trend = f"choppy (ADX {adx:.1f})"
    elif price_vs_ema50 > 0.02 and price_vs_ema200 > 0.01:
        regime = Regime.BULL
        multiplier = 1.0
        score = min(1.0, 0.5 + trend_score * 5)  # stronger trend = more confident
        index_trend = f"bullish ({price_vs_ema50:+.1%} vs EMA50)"
    else:
        regime = Regime.BEAR
        multiplier = 0.7
        score = min(1.0, 0.5 + abs(trend_score) * 3)
        index_trend = f"bearish ({price_vs_ema50:+.1%} vs EMA50)"

    return RegimeResult(
        regime=regime,
        score=score,
        multiplier=multiplier,
        index_trend=index_trend,
        factors=factors,
    )


def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Compute ADX from OHLC DataFrame. Simplified version."""
    if len(df) < period + 1:
        return 0.0

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # True range
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    # Directional movement
    dm_up = high - high.shift()
    dm_down = low.shift() - low
    dm_up = dm_up.apply(lambda x: x if x > 0 else 0)
    dm_down = dm_down.apply(lambda x: x if x > 0 else 0)

    # Filter: only take dm_up if it's greater than dm_down
    dm_up = dm_up.where(dm_up > dm_down, 0)
    dm_down = dm_down.where(dm_down > dm_up, 0)  # noqa: F841

    # Smoothed DM
    smooth_up = dm_up.rolling(window=period).mean()
    smooth_down = dm_down.rolling(window=period).mean()

    # DI+ and DI-
    di_plus = 100 * smooth_up / atr if atr.iloc[-1] > 0 else 0
    di_minus = 100 * smooth_down / atr if atr.iloc[-1] > 0 else 0

    # DX
    dx_sum = abs(di_plus) + abs(di_minus)
    dx = pd.Series(0.0, index=di_plus.index)
    valid = dx_sum > 0
    dx[valid] = 100 * abs(di_plus[valid] - di_minus[valid]) / dx_sum[valid]

    # ADX is smoothed DX (use just DX as approximation for simplicity)
    return float(dx.iloc[-1]) if not pd.isna(dx.iloc[-1]) else 0.0
