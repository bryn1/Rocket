"""MC 11185 (re-audit C2 / d3 F1): NaN never becomes a bullish max score.

Fix: ``rocket/technical/base.py normalize_score`` coerces NaN to neutral 0.0
(NaN slips through both max and min, so the bare clamp turned it into +1.0).
The page-score path is the WIRED island seam ``nightly_score.score_row``
(MC 11474 B2, PLAN-c3 §3 — the same seam scripts/score_all.py runs nightly;
the old app._compute_all_indicators seam retired with the wire commit) — no
source/data needed here: the real-ticker pins run on the committed frozen
store tests/data/golden35.

Red/green record (planted-bad run done on the pre-fix tree, 2026-10-23):
  * normalize_score(nan)              pre-fix 1.0  -> post-fix 0.0
  * 30-bar all-NaN page score         pre-fix 79.44 -> post-fix 51.67
    (the audit's d3 P1.4 frame scored 70.77 — the "~70.8" of the finding;
    the exact number is frame-shape dependent, the disease is not)
  * 1-bar all-NaN page score          pre-fix 60.28 -> post-fix 50.0
Those two page numbers are the OLD seam's history; through the wired island
the honest-neutral values are 49.82 (30 bars) and 49.685 (1 bar) — pinned
below; the disease (NaN -> bullish max) is what the unit guard pins, and it
holds on every seam.
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
    """Score through the WIRED page seam (nightly_score.score_row — the seam
    scripts/score_all.py runs nightly; MC 11474 B2). Returns the row dict."""
    import nightly_score
    return nightly_score.score_row(df, ticker, region, social=False,
                                   index_df=None, index_name="^GSPC")


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


# ── regression: degenerate frames score honest neutral on the wired path ────

def test_all_nan_frame_page_score_is_neutral_49_82_not_bullish():
    """30-bar all-NaN frame through the wired island: 49.82 — the island's
    honest neutral (old seam pinned 51.67, MC 11185 history in the module
    docstring). < 55 is the disease guard, not the literal."""
    row = _page_score(_nan_frame(30))
    assert row["overall"] == 49.82, row["overall"]
    assert row["overall"] < 55.0, "all-NaN must not read as a bullish page score"


def test_single_nan_bar_page_score_is_exact_neutral_49_685():
    """Empty-history case (1 NaN bar): old seam pinned 60.28 pre-fix / 50.0
    post-fix (MC 11185); through the wired island the exact honest neutral
    is 49.685 — never a bullish max."""
    row = _page_score(_nan_frame(1))
    assert row["overall"] == 49.685, row["overall"]


# ── non-NaN scoring must not move: full-precision pins, frozen fixtures ─────

def test_real_ticker_scores_full_precision_unchanged():
    """The golden-35 pin rounds to .1f; this pins the SAME frozen store bytes
    at full float precision through the WIRED seam (v2, MC 11474 B2), so any
    drift off the island path goes RED at repr level — the values are
    deterministic (B1: test_golden35_deterministic_twice_via_adapter)."""
    import nightly_score
    import store_io
    import full_universe
    pins = {
        "MSFT":      ("usa",    53.805, 56.25, 55.00000000000001, 3.0, 83.33500000000001),
        "SAAB-B.ST": ("sweden", 56.66,  62.5,  67.5,              3.0, 83.33500000000001),
        "BMW.DE":    ("germany", 44.214999999999996, 62.5, 67.5,  3.0, 66.66499999999999),
    }
    for ticker, (region, overall, mom, trend, vol, volm) in pins.items():
        path = GOLDEN35_DIR / full_universe.cache_filename(ticker)
        assert path.exists(), f"frozen golden-35 fixture missing: {path}"
        row = nightly_score.score_row(store_io.read_cache(path), ticker, region,
                                      social=False, index_df=None,
                                      index_name="^GSPC")
        assert row["overall"] == overall, ticker
        assert (row["momentum"], row["trend"],
                row["volatility"], row["volume"]) == (mom, trend, vol, volm), ticker
        # the published golden string still holds (cross-check, not a re-pin)
        assert f"{row['overall']:.1f}" == GOLDEN_35[ticker], ticker
