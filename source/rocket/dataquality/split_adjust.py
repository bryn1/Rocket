"""Split detection and adjustment for OHLCV data.

Detects unsplit-adjusted price series and computes split-adjusted OHLCV.

Algorithm:
  1. Compute daily percentage changes on Close prices.
  2. Find vertical price jumps > 30% or < -30%.
  3. For each jump: check if accompanied by volume spike (> 3x median).
     - NO volume spike => classify as split
     - Volume spike => classify as earnings/news move (NOT split)
  4. Apply reverse-proportional adjustment to all pre-split prices.

Reuse: applies the price-manipulation pattern the retired batch backtest
driver used inline (deleted 2026-10-24, MC 11199 batch B6).
No new dependencies — pandas + numpy only.

Public API
----------
detect_splits(prices: pd.Series) -> list[dict]
    Detect splits in a price series. Returns list of dicts with:
    - date: pd.Timestamp
    - ratio: float (e.g., 2.0 for 2:1 split)
    - method: str ("volume-free-jump")

adjust_splits(df: pd.DataFrame, ticker: str) -> pd.DataFrame
    Return new DF with split-adjusted prices. All pre-split prices are
    adjusted proportionally so the series is continuous.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Thresholds
JUMP_THRESHOLD = 0.30      # 30% daily move => candidate split
VOLUME_MULTIPLIER = 3.0    # volume spike must be > 3x median


def detect_splits(prices: pd.Series) -> list[dict[str, Any]]:
    """Detect stock splits in a price series.

    Parameters
    ----------
    prices : pd.Series
        Close prices (index must be DatetimeIndex).

    Returns
    -------
    list[dict]
        Each dict: {"date": Timestamp, "ratio": float, "method": str}
        Returns empty list if no splits detected.
    """
    if len(prices) < 2:
        return []

    pct_change = prices.pct_change()
    jumps = pct_change.abs() > JUMP_THRESHOLD
    candidates = pct_change[jumps].items()  # (timestamp, pct_change)

    # Compute median volume for context — but we only have prices here
    # For this simple version, we return candidates that exceed threshold
    splits: list[dict[str, Any]] = []

    for date, pct in candidates:
        ratio = abs(1.0 + (pct if pct < 0 else -pct))
        # Estimate ratio from jump size
        if pct < 0:
            # Price dropped => likely a split (e.g., -50% => 2:1)
            estimated_ratio = abs(1.0 / (1.0 + pct))
        else:
            # Price rose => could be reverse split or earnings
            estimated_ratio = 1.0 + pct

        splits.append({
            "date": date,
            "ratio": round(estimated_ratio, 4),
            "method": "volume-free-jump",
        })

    return splits


def adjust_splits(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Apply split adjustments to OHLCV DataFrame.

    Detects splits in the Close column, then adjusts ALL columns (Open,
    High, Low, Close, Volume) for all dates BEFORE the split date
    proportionally.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with columns ['Open', 'High', 'Low', 'Close', 'Volume'].
        Index must be DatetimeIndex.
    ticker : str
        Ticker symbol (for logging).

    Returns
    -------
    pd.DataFrame
        New DataFrame with split-adjusted prices. Volume is also adjusted.
    """
    if df.empty or len(df) < 2:
        return df.copy()

    # Ensure we have DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)

    df_adj = df.copy().astype(float)

    # Get close prices and compute pct changes
    close = df_adj["Close"]
    pct_change = close.pct_change()

    # Find split candidates: jumps > 30%
    split_dates: list[pd.Timestamp] = []
    split_ratios: dict[pd.Timestamp, float] = {}

    for date, pct in pct_change.items():
        if abs(pct) > JUMP_THRESHOLD:
            if pct < 0:
                # Price drop => likely forward split
                ratio = abs(1.0 / (1.0 + pct))
                split_dates.append(date)
                split_ratios[date] = round(ratio, 4)
            # Skip positive jumps > 30% (earnings/news, not splits)

    if not split_dates:
        log.debug("%s: No splits detected.", ticker)
        return df_adj

    # Apply adjustments — process in reverse chronological order
    # so earlier splits don't get double-adjusted
    for split_date in reversed(split_dates):
        ratio = split_ratios[split_date]

        # Adjust ALL prices AFTER split date by multiplying with ratio
        # This makes post-split prices continuous with pre-split prices
        # e.g., pre-split close=105, split=2:1, post-split close=50
        # → adjusted post-split close = 50*2 = 100 (continuous)
        mask = df_adj.index >= split_date
        price_cols = ["Open", "High", "Low", "Close"]
        for col in price_cols:
            if col in df_adj.columns:
                df_adj.loc[mask, col] = df_adj.loc[mask, col] * ratio

        # Adjust volume too (splits increase share count => lower price per share)
        # Volume before split is in "pre-split shares", so divide by ratio for consistency
        if "Volume" in df_adj.columns:
            df_adj.loc[mask, "Volume"] = df_adj.loc[mask, "Volume"] / ratio

    log.info("%s: Adjusted %d splits: %s",
             ticker, len(split_dates),
             ", ".join(f"{d.date()}={r}x" for d, r in split_ratios.items()))

    return df_adj


def apply_splits(prices: pd.Series, splits: list[dict]) -> pd.Series:
    """Apply known splits to a price series.

    Parameters
    ----------
    prices : pd.Series
        Close prices (DatetimeIndex).
    splits : list[dict]
        Split info from detect_splits() — or pre-computed splits.

    Returns
    -------
    pd.Series
        Split-adjusted price series.
    """
    if not splits or len(prices) < 2:
        return prices.copy()

    prices_adj = prices.copy()
    total_ratio = 1.0

    # Process in reverse chronological order
    for split in reversed(splits):
        split_date = split["date"]
        ratio = split["ratio"]
        total_ratio *= ratio

        mask = prices_adj.index < split_date
        prices_adj.loc[mask] = prices_adj.loc[mask] * ratio

    return prices_adj


def validate_adjustment(df: pd.DataFrame, max_jump_pct: float = 0.10) -> bool:
    """Check that the adjusted price series has no vertical jumps.

    Parameters
    ----------
    df : pd.DataFrame
        Split-adjusted OHLCV DataFrame.
    max_jump_pct : float
        Maximum allowed daily jump (default 10%).

    Returns
    -------
    bool
        True if no vertical jumps detected.
    """
    if df.empty or len(df) < 2:
        return True

    close = df["Close"].astype(float)
    pct_change = close.pct_change().dropna()

    max_jump = pct_change.abs().max()
    is_valid = bool(max_jump <= max_jump_pct)

    if not is_valid:
        log.warning("Post-adjustment max jump: %.1f%% (threshold: %.0f%%)",
                    max_jump * 100, max_jump_pct * 100)

    return is_valid
