"""MC 11185 (re-audit C2 / d3 F1): NaN never becomes a bullish max score.

Fix: ``rocket/technical/base.py normalize_score`` coerces NaN to neutral 0.0
(NaN slips through both max and min, so the bare clamp turned it into +1.0).
The page-score path is the shipped seam ``app._compute_all_indicators`` +
``app._score_from_summary`` (same seam as scripts/score_all.py and the
golden-35 pin) — no source/data needed here: the real-ticker pins run on the
committed frozen store tests/data/golden35.

Red/green record (planted-bad run done on the pre-fix tree, 2026-10-23):
  * normalize_score(nan)              pre-fix 1.0  -> post-fix 0.0
  * 30-bar all-NaN page score         pre-fix 79.44 -> post-fix 51.67
    (the audit's d3 P1.4 frame scored 70.77 — the "~70.8" of the finding;
    the exact number is frame-shape dependent, the disease is not)
  * 1-bar all-NaN page score          pre-fix 60.28 -> post-fix 50.0
Honest neutral, not 50.0: MFI masks NaN flows to 0 with .where(...) and reads
0.0 "oversold" -> BUY 1.0 (volume family avg 0.3333). That NaN masking is a
separate finding from F1 (normalize_score) and stays out of this fix's scope.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TESTS = Path(__file__).resolve().parent
for _p in (str(TESTS), str(TESTS.parent / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rocket.technical.base import normalize_score
from demo_page_fixtures import GOLDEN35_DIR, GOLDEN_35    # noqa: E402 (pin asserts import)


def _nan_frame(n: int) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"date": idx, "open": np.nan, "high": np.nan,
                         "low": np.nan, "close": np.nan, "volume": np.nan})


def _page_score(df: pd.DataFrame, ticker: str = "NANTEST", region: str = "usa"):
    """Score through the shipped page seam (identical to scripts/score_all.py)."""
    import app
    summary, _ = app._compute_all_indicators(df)
    return app._score_from_summary(summary, ticker=ticker,
                                   region=region)["rocket_score"]


# ── the unit guard: NaN -> neutral, never max ───────────────────────────────

def test_normalize_score_nan_is_neutral_zero_not_max():
    """Planted-bad guard: pre-fix this returned 1.0 (full-strength bullish)."""
    assert normalize_score(float("nan")) == 0.0
    assert normalize_score(np.nan) == 0.0


def test_normalize_score_reals_and_inf_clamp_unchanged():
    """Clamp semantics for non-NaN inputs are pinned byte-for-byte (F1 fix
    must not move them; +/-inf still clamp to +/-1.0 — inf is NOT NaN)."""
    assert normalize_score(0.3) == 0.3
    assert normalize_score(1.7) == 1.0
    assert normalize_score(-2.2) == -1.0
    assert normalize_score(float("inf")) == 1.0
    assert normalize_score(float("-inf")) == -1.0


# ── regression: degenerate frames score honest neutral on the page path ─────

def test_all_nan_frame_page_score_is_neutral_51_67_not_bullish():
    """30-bar all-NaN frame: pre-fix 79.44 (audit frame shape: ~70.8);
    post-fix the honest neutral is 51.67 — see module docstring for why the
    residual +1.67 is MFI .where-masking, not normalize_score."""
    rs = _page_score(_nan_frame(30))
    assert rs.overall_score == 51.67, rs.overall_score
    assert rs.overall_score < 55.0, "all-NaN must not read as a bullish page score"


def test_single_nan_bar_page_score_is_exact_neutral_50_0():
    """Empty-history case (1 NaN bar): pre-fix 60.28, post-fix exactly 50.0."""
    rs = _page_score(_nan_frame(1))
    assert rs.overall_score == 50.0, rs.overall_score


# ── non-NaN scoring must not move: full-precision pins, frozen fixtures ─────

def test_real_ticker_scores_full_precision_unchanged():
    """The golden-35 pin rounds to .1f; this pins the SAME frozen store bytes
    at full float precision across BOTH scorers, so any drift off the F1 fix
    (verified byte-equal pre/post, 2026-10-23) goes RED at repr level."""
    import app
    import store_io
    import full_universe
    from rocket.backtest.indicator_eval import to_indicator_frame
    pins = {
        "MSFT":      ("usa",    57.83, 0.0945, 0.1561, 0.2337, 0.4312),
        "SAAB-B.ST": ("sweden", 64.23, 0.402,  0.1623, 0.3116, 0.1116),
        "BMW.DE":    ("germany", 39.13, -0.2459, -0.1998, -0.3415, -0.0735),
    }
    for ticker, (region, overall, mom, trend, vol, volm) in pins.items():
        path = GOLDEN35_DIR / full_universe.cache_filename(ticker)
        assert path.exists(), f"frozen golden-35 fixture missing: {path}"
        idf = to_indicator_frame(store_io.read_cache(path))
        summary, _ = app._compute_all_indicators(idf)
        rs = app._score_from_summary(summary, ticker=ticker,
                                     region=region)["rocket_score"]
        assert rs.overall_score == overall, ticker
        assert (summary.momentum_score, summary.trend_score,
                summary.volatility_score, summary.volume_score) == (mom, trend, vol, volm), ticker
        # the published golden string still holds (cross-check, not a re-pin)
        assert f"{rs.overall_score:.1f}" == GOLDEN_35[ticker], ticker
