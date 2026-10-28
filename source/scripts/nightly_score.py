"""Nightly adapter — the seam from a store frame to the 34-indicator island.

PLAN-c3 §3 (MC 11474 option-B wiring): ONE mapping authority between the
CSV-store frame and ``compute_rocket_score`` (``rocket/scoring/rocket_score.py:204``),
plus the D2/D3 explicit-flag choke point:

* ``social`` is a REQUIRED keyword with no default (D2: every call site passes
  the flag explicitly — a single-ticker caller gets TypeError, never a hidden
  default);
* ``social=True`` additionally supplies the short per-call HTTP timeout
  ``N = SOCIAL_TIMEOUT_S = 2.0 s`` (D2 safety ruling; the library keeps 30.0
  for direct callers — C2 pin in tests/test_nightly_score.py);
* options/fundamentals pass OFF EXPLICITLY at this one choke point (D3 —
  ``options_factor=False``, ``fundamental_data=None``, not silent defaults;
  each future re-add is its own owner-visible card).

The returned row carries EXACTLY the keys ``score_all.py:72-77`` consumes today
({ticker, signal, overall, momentum, trend, volatility, volume, close}); the
renderer contract (demo_render.py:189-192, :103-104) stays unchanged.
``index_df``/``index_name`` mirror compute_rocket_score's own parameters and
are forwarded untouched (C1 — the B2 driver loads the index CSVs and supplies
them on the nightly; single-ticker callers keep the defaults).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rocket.backtest.indicator_eval import to_indicator_frame  # noqa: E402
from rocket.data.models import Region, TickerInfo  # noqa: E402
from rocket.scoring.rocket_score import (  # noqa: E402
    SignalStrength,
    compute_rocket_score,
)
from rocket.scoring.weighter import _to_0_100  # noqa: E402 — PLAN-c3 §3 names
# this exact shape (weighter.py:20-24): the [-1,1] → [0,100] map, reused, never
# re-implemented beside it.

# D2 safety ruling, named (PLAN-c3 §3/§6 B1 "N = 2.0 s (named)"): with no
# sentiment service on the host a call is instant-refusal; a HANGING listener
# must be bounded by this, never by the library's 30.0 default.
SOCIAL_TIMEOUT_S = 2.0

# Page signals: the pipeline's hysteresis thresholds already live in
# _strength_from_score (rocket_score.py:185-201); the adapter only maps the
# resulting strength to the three strings demo_render.py:103-104 classifies.
_STRENGTH_TO_SIGNAL = {
    SignalStrength.STRONG_BULLISH: "BUY",
    SignalStrength.BULLISH: "BUY",
    SignalStrength.BEARISH: "SELL",
    SignalStrength.STRONG_BEARISH: "SELL",
    SignalStrength.MODERATE: "HOLD",
    SignalStrength.WEAK: "HOLD",
    SignalStrength.NEUTRAL: "HOLD",
}


def _family_score(family_votes: list[dict], family: str) -> float:
    """One family's 0-100 score from rocket_signal.family_votes.

    A family's signed strength (BUY=+, SELL=-, HOLD=0, each |strength|<=1)
    mapped through the weighter's _to_0_100 shape; absent family → neutral.
    """
    signed = 0.0
    for fv in family_votes or []:
        if fv.get("family") == family:
            if fv.get("vote") == "BUY":
                signed = fv.get("strength", 0.0)
            elif fv.get("vote") == "SELL":
                signed = -fv.get("strength", 0.0)
            else:
                signed = 0.0
    return _to_0_100(signed)


def score_row(df, ticker: str, region: str, *, social: bool,
              index_df=None, index_name: str = "^GSPC") -> dict:
    """Score one store frame through the island; return the page row dict.

    ``social`` is REQUIRED (no default, D2). ``region`` rides the driver's
    task tuple: TickerInfo's 4-bucket Region enum is NOT a scoring input
    (filter.py consumes only price/avg_volume/market_cap) and the region→index
    mapping rides ``index_df``/``index_name`` (C1), so it stays informational
    here. Raises with the frame — the driver owns the error meta (§5 F3).
    """
    idf = to_indicator_frame(df)          # same conversion the seam used
    close = round(float(idf["Close"].iloc[-1]), 2)   # score_all.py:76
    kwargs = {
        "social_sentiment": social,   # D2: explicit flag, never the :330 default
        "options_factor": False,      # D3: explicit OFF at the choke point
        "fundamental_data": None,     # D3: explicit OFF at the choke point
    }
    if social:
        kwargs["social_timeout"] = SOCIAL_TIMEOUT_S   # D2 safety bound, N named
    result = compute_rocket_score(
        idf,
        TickerInfo(ticker=ticker, name=ticker, region=Region.US),
        current_price=float(close),
        index_df=index_df,
        index_name=index_name,
        **kwargs,
    )
    signal = result["rocket_signal"]
    family_votes = signal.family_votes or []
    # Row keys EXACTLY score_all.py:72-77; mappings per PLAN-c3 §3:
    return {
        "ticker": ticker,
        "signal": _STRENGTH_TO_SIGNAL[signal.signal],   # strength/thresholds
        "overall": _to_0_100(result["rocket_score"]),   # (final+1)/2*100
        "momentum": _family_score(family_votes, "momentum"),
        "trend": _family_score(family_votes, "trend"),
        # volatility from risk_result; risk_score is 0.0-1.0 (risk.py:24)
        "volatility": float(result["risk_result"].risk_score) * 100.0,
        "volume": _family_score(family_votes, "volume"),
        "close": close,
    }
