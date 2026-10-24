"""Pipeline orchestration layer for data quality.

Orchestrates split adjustment + outlier handling + validation into a single
clean pipeline — in place of the inline quality filters the retired batch
backtest driver carried (deleted 2026-10-24, MC 11199 batch B6).

Public API
----------
validate_ohlcv(df, ticker) -> dict
clean_ohlcv(df, ticker) -> tuple[pd.DataFrame, dict]
build_pipeline_config() -> dict
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import pandas as pd

from rocket.dataquality.split_adjust import adjust_splits, validate_adjustment
from rocket.dataquality.outlier_detect import (
    winsorize_df,
    outlier_report,
)

log = logging.getLogger(__name__)

# Pipeline configuration defaults
DEFAULT_CONFIG = {
    "split_threshold": 0.30,       # 30% jump => split candidate
    "volume_multiplier": 3.0,      # volume spike must be > 3x median
    "outlier_k": 1.5,              # IQR multiplier
    "outlier_mad_threshold": 3.0,  # MAD multiplier
    "winsor_pct": 0.01,            # 1st/99th percentile
    "winsor_columns": ["Close", "High", "Low", "Open", "Volume"],
    "max_position_pct": 5.0,       # max position as % of capital
    "risk_per_trade_pct": 1.0,     # risk per trade as % of capital
    "atr_period": 14,              # ATR lookback period
}


def validate_ohlcv(df: pd.DataFrame, ticker: str) -> dict[str, Any]:
    """Validate OHLCV DataFrame for quality issues.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (must have ['Open', 'High', 'Low', 'Close', 'Volume']).
    ticker : str
        Ticker symbol (for logging).

    Returns
    -------
    dict
        {
            "pass": bool,
            "ticker": str,
            "issues": list[str],
            "outlier_report": dict,
        }
    """
    issues: list[str] = []

    # Check for empty data
    if df.empty:
        return {
            "pass": False,
            "ticker": ticker,
            "issues": ["Empty DataFrame"],
            "outlier_report": {},
        }

    # Check required columns
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        issues.append(f"Missing columns: {missing}")

    # Check for negative prices (except Volume which can be 0)
    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            neg_count = (df[col] < 0).sum()
            if neg_count > 0:
                issues.append(f"{col}: {neg_count} negative values")

    # Check for NaN
    for col in required:
        if col in df.columns:
            nan_count = df[col].isna().sum()
            if nan_count > 0:
                issues.append(f"{col}: {nan_count} NaN values")

    cols_to_check = [c for c in required if c in df.columns]
    report = outlier_report(df, cols_to_check)

    return {
        "pass": len(issues) == 0,
        "ticker": ticker,
        "issues": issues,
        "outlier_report": report,
    }


def clean_ohlcv(df: pd.DataFrame, ticker: str) -> Tuple[pd.DataFrame, dict[str, Any]]:
    """Clean OHLCV DataFrame through the full pipeline.

    Pipeline steps:
    1. Validate input data
    2. Detect and adjust splits
    3. Winsorize outliers
    4. Validate post-cleaning

    Parameters
    ----------
    df : pd.DataFrame
        Raw OHLCV DataFrame.
    ticker : str
        Ticker symbol (for logging).

    Returns
    -------
    tuple[pd.DataFrame, dict]
        (cleaned_df, metadata) where metadata contains:
        - split_adjusted: bool
        - outliers_winsorized: bool
        - columns_modified: list[str]
        - rejected: bool
        - rejection_reason: str | None
        - atr_at_start: float | None
    """
    metadata: dict[str, Any] = {
        "split_adjusted": False,
        "outliers_winsorized": False,
        "columns_modified": [],
        "rejected": False,
        "rejection_reason": None,
        "atr_at_start": None,
    }

    # Step 1: Validate
    validation = validate_ohlcv(df, ticker)
    if not validation["pass"]:
        metadata["rejected"] = True
        metadata["rejection_reason"] = "; ".join(validation["issues"])
        log.warning("%s: Rejected — %s", ticker, metadata["rejection_reason"])
        return df, metadata

    # Step 2: Split adjustment
    try:
        df_adj = adjust_splits(df, ticker)
        metadata["split_adjusted"] = True

        # Validate adjustment
        if "Close" in df_adj.columns:
            is_valid = validate_adjustment(df_adj, max_jump_pct=0.10)
            if not is_valid:
                log.warning("%s: Split adjustment left jumps > 10%%", ticker)
    except Exception as e:
        log.warning("%s: Split adjustment failed: %s", ticker, e)
        df_adj = df.copy()

    # Step 3: Winsorize outliers
    try:
        cols_to_winsorize = [c for c in DEFAULT_CONFIG["winsor_columns"]
                            if c in df_adj.columns]
        df_winsorized = winsorize_df(df_adj, cols_to_winsorize,
                                    clip_pct=DEFAULT_CONFIG["winsor_pct"])
        metadata["outliers_winsorized"] = True
        metadata["columns_modified"] = cols_to_winsorize
    except Exception as e:
        log.warning("%s: Winsorization failed: %s", ticker, e)
        df_winsorized = df_adj.copy()

    # Step 4: Compute ATR at start
    if "High" in df_winsorized.columns and "Low" in df_winsorized.columns:
        from rocket.dataquality.position_size import compute_atr_from_df
        atr_val = compute_atr_from_df(df_winsorized, period=DEFAULT_CONFIG["atr_period"])
        metadata["atr_at_start"] = atr_val

    log.info("%s: Cleaned — split_adj=%s, winsorized=%s, atr=%.2f",
             ticker, metadata["split_adjusted"],
             metadata["outliers_winsorized"],
             metadata["atr_at_start"] or 0.0)

    return df_winsorized, metadata


def build_pipeline_config() -> dict[str, Any]:
    """Build and return the pipeline configuration.

    Returns
    -------
    dict
        Configuration dict with all module parameters.
    """
    return dict(DEFAULT_CONFIG)
