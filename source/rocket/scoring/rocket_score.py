"""Compute full RocketScore for a single ticker using the v2 pipeline.

Pipeline:
  1. Run all 11 indicators
  2. Convert indicator results → IndicatorVotes
  3. Compute family consensus → DirectionResult
  4. Compute confidence from family agreement + signal strength
  5. Compute risk from ATR + Bollinger squeeze + volume anomaly
  6. Apply regime multiplier
  7. Final score = direction × confidence × risk_multiplier × regime_multiplier
  8. Apply hysteresis thresholds
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import logging

logger = logging.getLogger(__name__)

from ..data.models import TickerInfo
from ..technical.families import IndicatorVote, Vote, FamilyName, combine
from ..technical.signal_combiner import SignalCombiner  # kept for backward compat
from ..technical.momentum import RSI, MACD, ROC, Stochastic, WilliamsR, CCI
from ..technical.trend import EMACrossover, ADX, EMA9, EMA21, EMA50, EMA200
from ..technical.volatility import BollingerBands, ATR, DonchianChannel
from ..technical.volume import OBV, MFI, VWAPIndicator
from ..technical.advanced import IchimokuCloud, Supertrend, AutoTrend, RubeGoldberg, ParabolicSAR
from ..technical.pattern_match import EchoChamber, MatchFinder
from ..technical.composites import FearGreed, Chimera
from ..technical.ufvg import UnFairValueGapDetector
from ..technical.patterns import (
    DoubleTopBottom, HeadShoulders, WedgePattern,
    AutoFractal, CupAndHandle, PatternDetectorCombined,
)
from .filter import apply_filters
from .risk import compute_risk
from .confidence import compute_confidence
from .fundamentals_filter import evaluate_fundamentals, FundamentalFilterResult
from ..technical.regime import detect_regime, RegimeResult, Regime
from ..social.sentiment import get_social_sentiment, get_social_score
from ..quant.options import compute_options_factor


# All indicator instances (direction + risk)
INDICATORS = [
    # Momentum (6)
    RSI(), MACD(), ROC(), Stochastic(), WilliamsR(), CCI(),
    # Trend (14)
    EMACrossover(), ADX(), EMA9(), EMA21(), EMA50(), EMA200(),
    IchimokuCloud(), Supertrend(),
    AutoTrend(), RubeGoldberg(), ParabolicSAR(),
    DoubleTopBottom(), HeadShoulders(),
    WedgePattern(), AutoFractal(), CupAndHandle(),
    PatternDetectorCombined(),
    EchoChamber(), MatchFinder(), FearGreed(), Chimera(), UnFairValueGapDetector(),
    # Volatility (3) — risk-only, NOT in direction voting
    BollingerBands(), ATR(), DonchianChannel(),
    # Volume (3)
    OBV(), MFI(), VWAPIndicator(),
]

# Direction indicators ONLY — used for family consensus voting
# BB, ATR, and DonchianChannel are risk/volatility-only
DIRECTION_INDICATORS = [
    # Momentum (6)
    RSI(), MACD(), ROC(), Stochastic(), WilliamsR(), CCI(),
    # Trend (14)
    EMACrossover(), ADX(), EMA9(), EMA21(), EMA50(), EMA200(),
    IchimokuCloud(), Supertrend(),
    AutoTrend(), RubeGoldberg(), ParabolicSAR(),
    DoubleTopBottom(), HeadShoulders(),
    WedgePattern(), AutoFractal(), CupAndHandle(),
    PatternDetectorCombined(),
    EchoChamber(), MatchFinder(), FearGreed(), Chimera(), UnFairValueGapDetector(),
    # Volume (3)
    OBV(), MFI(), VWAPIndicator(),
]

# Mapping from indicator name → family
# BB and ATR intentionally NOT included — they are risk-only
_NAME_TO_FAMILY = {
    # Momentum (6)
    "RSI": FamilyName.MOMENTUM,
    "MACD": FamilyName.MOMENTUM,
    "ROC": FamilyName.MOMENTUM,
    "Stochastic": FamilyName.MOMENTUM,
    "Williams %R": FamilyName.MOMENTUM,
    "CCI": FamilyName.MOMENTUM,
    # Trend (14)
    "EMACrossover": FamilyName.TREND,
    "ADX": FamilyName.TREND,
    "EMA9": FamilyName.TREND,
    "EMA21": FamilyName.TREND,
    "EMA50": FamilyName.TREND,
    "EMA200": FamilyName.TREND,
    "Ichimoku": FamilyName.TREND,
    "IchimokuCloud": FamilyName.TREND,
    "Supertrend": FamilyName.TREND,
    "AutoTrend": FamilyName.TREND,
    "RubeGoldberg": FamilyName.TREND,
    "Parabolic SAR": FamilyName.TREND,
    "EchoChamber": FamilyName.TREND,
    "MatchFinder": FamilyName.TREND,
    "UnFairValueGap": FamilyName.TREND,
    "FearGreed": FamilyName.MOMENTUM,
    "Chimera": FamilyName.MOMENTUM,
    "DoubleTopBottom": FamilyName.TREND,
    "HeadShoulders": FamilyName.TREND,
    "WedgePattern": FamilyName.TREND,
    "AutoFractal": FamilyName.TREND,
    "CupAndHandle": FamilyName.TREND,
    "PatternDetector": FamilyName.TREND,
    "PatternDetectorCombined": FamilyName.TREND,
    # Volume (3)
    "OBV": FamilyName.VOLUME,
    "MFI": FamilyName.VOLUME,
    "VWAPIndicator": FamilyName.VOLUME,
}


class SignalStrength(str, Enum):
    STRONG_BULLISH = "Strong Bullish"
    BULLISH = "Bullish"
    MODERATE = "Moderate"
    NEUTRAL = "Neutral"
    WEAK = "Weak"
    STRONG_BEARISH = "Strong Bearish"
    BEARISH = "Bearish"


@dataclass
class RocketSignal:
    """Final output of the v2 scoring pipeline."""
    final_score: float  # -1.0 → +1.0 (direction × confidence × risk × regime)
    direction_score: float  # 0.0 → 1.0 (raw direction from family consensus)
    confidence: float  # 0.0 → 1.0
    risk_multiplier: float  # 0.5 → 1.0
    regime_multiplier: float  # 0.5 → 1.0
    signal: SignalStrength
    strength: str
    direction: str  # "BULLISH" or "BEARISH"
    regime: str  # "BULL", "BEAR", "CHOP"
    social_sentiment_score: float = 0.0  # -1.0 → +1.0 (social sentiment influence)
    family_votes: list[dict] | None = None  # detail per family
    reason: str = ""  # human-readable reason


def _indicator_result_to_vote(
    result,  # IndicatorResult
) -> IndicatorVote | None:
    """Convert an IndicatorResult to an IndicatorVote."""
    signal = result.signal.value.lower() if hasattr(result, 'signal') else result.get('signal', 'HOLD')
    vote = Vote.HOLD
    if signal in ("buy", "bullish"):
        vote = Vote.BUY
    elif signal in ("sell", "bearish"):
        vote = Vote.SELL

    strength = abs(result.score) if hasattr(result, 'score') else abs(result.get('score', 0.0))

    family_name_str = result.category.value.lower() if hasattr(result, 'category') else result.get('category', 'trend')
    # Map category to family
    cat_to_family = {
        "momentum": FamilyName.MOMENTUM,
        "trend": FamilyName.TREND,
        "volatility": FamilyName.TREND,  # volatility maps to trend family for voting
        "volume": FamilyName.VOLUME,
    }
    family = cat_to_family.get(family_name_str, FamilyName.TREND)

    return IndicatorVote(
        name=result.name if hasattr(result, 'name') else result.get('name', 'Unknown'),
        vote=vote,
        strength=strength,
        family=family,
    )


def _strength_from_score(final_score: float, confidence: float) -> tuple[str, str]:
    """Determine signal strength from final score and confidence."""
    abs_score = abs(final_score)

    if abs_score < 0.2:
        return SignalStrength.NEUTRAL.value, "Neutral"
    if confidence < 0.4:
        return SignalStrength.WEAK.value, "Weak"
    if abs_score < 0.4:
        return SignalStrength.MODERATE.value, "Moderate"
    if abs_score < 0.6:
        if final_score > 0:
            return SignalStrength.BULLISH.value, "Bullish"
        return SignalStrength.BEARISH.value, "Bearish"
    if final_score > 0:
        return SignalStrength.STRONG_BULLISH.value, "Strong Bullish"
    return SignalStrength.STRONG_BEARISH.value, "Strong Bearish"


def compute_rocket_score(
    df,
    ticker_info: TickerInfo,
    current_price: float = 0.0,
    index_df=None,  # optional: regional index data for regime
    index_name: str = "^GSPC",  # regional index ticker
    **kwargs,  # optional: fundamental_data, require_fundamentals
) -> dict:
    """Run all indicators, compute v2 pipeline scores, return dict."""
    # ─── Step 1: Run ALL indicators (direction + risk) ───
    results = []
    for indicator in INDICATORS:
        try:
            r = indicator.calculate(df)
            results.append(r)
        except Exception:
            logger.warning("Direction indicator calculation failed")

    # ─── Step 1b: Run direction indicators only for voting ───
    # BB and ATR are risk-only — they must NOT vote in family consensus
    direction_results = []
    for indicator in DIRECTION_INDICATORS:
        try:
            r = indicator.calculate(df)
            direction_results.append(r)
        except Exception:
            logger.warning("Direction indicator calculation failed")

    # ─── Step 2: Convert direction results to IndicatorVotes ───
    indicator_votes = []
    indicator_strengths = []
    for r in direction_results:
        try:
            vote = _indicator_result_to_vote(r)
            if vote:
                indicator_votes.append(vote)
                indicator_strengths.append(vote.strength)
        except Exception:
            logger.warning("Direction indicator calculation failed")

    # ─── Step 3: Family consensus → Direction ───
    direction_result, family_votes = combine(indicator_votes)

    # Separate volume family for confidence calc
    volume_families = [fv for fv in family_votes if fv.family == FamilyName.VOLUME]

    # ─── Step 4: Confidence ───
    confidence = compute_confidence(
        family_votes=family_votes,
        volume_votes=volume_families,
        indicator_strengths=indicator_strengths,
    )

    # ─── Step 5: Risk ───
    # Extract ATR and Bollinger values from results
    atr_val = 0.0
    bb_upper = bb_lower = bb_sma = 0.0
    vol_ratio = 1.0

    for r in results:
        name = r.name if hasattr(r, 'name') else r.get('name', '')
        vals = r.values if hasattr(r, 'values') else r.get('values', {})
        if name == "ATR":
            atr_val = r.score if hasattr(r, 'score') else r.get('score', 0.0)
        elif name in ("BollingerBands", "Bollinger Bands"):
            bb_upper = vals.get('upper', 0.0) or vals.get('upper_band', 0.0)
            bb_lower = vals.get('lower', 0.0) or vals.get('lower_band', 0.0)
            bb_sma = vals.get('sma', 0.0)
        elif name == "VWAPIndicator":
            # Volume info from VWAP indicator
            vol_data = vals.get('volume_ratio', None)
            if vol_data is not None:
                vol_ratio = vol_data

    # Calculate ATR% and Bollinger squeeze
    atr_pct = (atr_val / current_price * 100) if current_price > 0 else 0.0
    bb_squeeze = (bb_upper - bb_lower) / bb_sma if bb_sma > 0 else 0.05

    # Volume anomaly
    if vol_ratio == 1.0:
        vol_ratio = 1.0  # default if no data

    risk = compute_risk(
        atr_pct=atr_pct,
        bb_squeeze=bb_squeeze,
        volume_ratio=vol_ratio,
    )

    # ─── Step 6: Regime ───
    if index_df is not None and not index_df.empty:
        regime = detect_regime(index_df, index_name)
    else:
        regime = RegimeResult(
            regime=Regime.CHOP,
            score=0.0,
            multiplier=0.5,
            index_trend="no index data",
            factors=["no index data"],
        )

    # ─── Step 7: Fundamentals (optional) ───
    fd = kwargs.get('fundamental_data')
    fund_result: FundamentalFilterResult | None = None
    if fd is not None:
        fund_result = evaluate_fundamentals(fd)
        if not fund_result.is_pass and kwargs.get('require_fundamentals', False):
            return {
                "rocket_score": 0.0,
                "signal_summary": None,
                "filter_result": None,
                "rocket_signal": RocketSignal(
                    final_score=0.0,
                    direction_score=0.0,
                    confidence=0.0,
                    risk_multiplier=1.0,
                    regime_multiplier=1.0,
                    signal=SignalStrength.NEUTRAL,
                    strength="Neutral",
                    direction="NEUTRAL",
                    regime="NO_DATA",
                    reason=f"Rejected: {fund_result.rejection_reason}",
                ),
                "direction_result": None,
                "risk_result": None,
                "confidence_result": None,
                "regime_result": None,
                "fundamental_result": fund_result,
            }
    elif fd is not None:
        pass  # fundamentals available but not required

    # ─── Step 7b: Social Sentiment (optional influence) ───
    social_score: float = 0.0
    social_sentiment_data: dict[str, Any] = {}
    social_enabled = kwargs.get('social_sentiment', True)
    if social_enabled:
        try:
            ticker_symbols = [ticker_info.ticker.split('.')[0] if '.' in ticker_info.ticker else ticker_info.ticker]
            sentiment_map = get_social_sentiment(ticker_symbols)
            if ticker_symbols[0] in sentiment_map:
                ss = sentiment_map[ticker_symbols[0]]
                social_score = get_social_score(ss)  # -1.0 to +1.0
                social_sentiment_data = {
                    'sentiment': ss.overall_sentiment,
                    'score': ss.sentiment_score,
                    'mentions': ss.total_mentions,
                }
        except Exception:
            social_score = 0.0  # default neutral if scraping fails

    # ─── Step 7c: Options Factor (optional influence) ───
    options_score: float = 0.0
    options_data: dict[str, Any] = {}
    options_enabled = kwargs.get('options_factor', False)
    if options_enabled and current_price > 0:
        try:
            opts = compute_options_factor(
                ticker=ticker_info.ticker.split('.')[0] if '.' in ticker_info.ticker else ticker_info.ticker,
                current_price=current_price,
                social_sentiment_score=social_score,
            )
            if opts.is_options_enabled:
                options_score = opts.bias
                options_data = {
                    'max_pain_distance': opts.max_pain_distance,
                    'put_call_ratio': opts.put_call_ratio,
                    'dte': opts.dte,
                    'bias': opts.bias,
                    'confidence': opts.confidence,
                    'expiration': opts.details.get('expiration'),
                    'max_pain_strike': opts.details.get('max_pain_strike'),
                }
        except Exception:
            options_score = 0.0  # default neutral if options fetch fails

    # ─── Step 8: Final score ───
    # direction_score: 0.0→1.0, convert to -1.0→+1.0
    direction_signed = 2.0 * direction_result.score - 1.0  # normalize to [-1, 1]

    final_score = (
        direction_signed
        * confidence.confidence_score
        * risk.risk_multiplier
        * regime.multiplier
    )

    # Apply social sentiment as a small bias (max ±0.1 influence on final score)
    if social_score != 0.0:
        social_bias = social_score * 0.1  # 10% max influence
        final_score += social_bias

    # Apply options factor as a small bias (max ±0.05 influence on final score)
    # Options data is less reliable than technical signals
    if options_score != 0.0:
        options_bias = options_score * 0.05  # 5% max influence
        final_score += options_bias

    # Clamp to [-1, 1]
    final_score = max(-1.0, min(1.0, final_score))

    # ─── Step 9: Signal strength ───
    strength, strength_label = _strength_from_score(final_score, confidence.confidence_score)
    direction = "BULLISH" if final_score > 0 else "BEARISH"

    # Build reason string
    [
        f"{fv.family.value}: {fv.vote.value} ({fv.strength:+.2f})"
        for fv in family_votes
    ]
    reason_parts = [
        f"{strength_label} ({direction}) | ",
        f"dir={direction_result.score:.2f} conf={confidence.confidence_score:.2f} ",
        f"risk={risk.risk_score:.2f} regime={regime.regime.value}",
    ]
    if fund_result is not None:
        reason_parts.append(f" fund={fund_result.quality}({fund_result.score:.2f})")
    if social_score != 0.0:
        reason_parts.append(f" social={social_score:+.2f}")
    if options_score != 0.0:
        reason_parts.append(f" opts={options_score:+.2f} mp={options_data.get('max_pain_strike', '?')}")

    # Convert family_votes to dicts for serialization
    family_vote_dicts = []
    for fv in family_votes:
        family_vote_dicts.append({
            "family": fv.family.value,
            "vote": fv.vote.value,
            "strength": round(fv.strength, 4),
            "count": fv.indicators_count,
        })

    rocket_signal = RocketSignal(
        final_score=round(final_score, 4),
        direction_score=round(direction_result.score, 4),
        confidence=round(confidence.confidence_score, 4),
        risk_multiplier=round(risk.risk_multiplier, 4),
        regime_multiplier=round(regime.multiplier, 4),
        signal=SignalStrength(strength),
        strength=strength_label,
        direction=direction,
        regime=regime.regime.value,
        social_sentiment_score=round(social_score, 4),
        family_votes=family_vote_dicts,
        reason="".join(reason_parts),
    )

    # Keep old SignalSummary for backward compatibility
    combiner = SignalCombiner()
    old_summary = combiner.combine(results)

    # Keep old weighter for backward compatibility
    filter_result = apply_filters(ticker_info, current_price)

    return {
        "rocket_score": round(final_score, 4),
        "signal_summary": old_summary,
        "filter_result": filter_result,
        "rocket_signal": rocket_signal,
        "direction_result": direction_result,
        "risk_result": risk,
        "confidence_result": confidence,
        "regime_result": regime,
        "fundamental_result": fund_result,
        "social_sentiment": social_sentiment_data if social_score != 0.0 else None,
        "options_data": options_data if options_score != 0.0 else None,
    }
