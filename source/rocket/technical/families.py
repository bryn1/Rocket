"""Family consensus voting — group indicators into independent families.

Each family (Trend, Momentum, Volume) votes independently.
Within each family, majority rules. Between families, weighted average.

Architecture (evidence-based weighting):
  Momentum (6 indicators) → 50% weight  [Tier 1: Jegadeesh & Titman 1993, Moskowitz et al. 2012]
  Trend (14 indicators)   → 35% weight  [Tier 2: Chan 2007, Aronson 2011]
  Volume (3 indicators)   → 15% weight  [Tier 3: weak evidence for directional prediction]

Each indicator votes: BUY (+1), HOLD (0), SELL (-1)
Family vote: sum of votes / max_possible (normalizes to [-1, 1])
Direction score: weighted sum of family votes → normalized to [0, 1]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


class Vote(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class FamilyName(str, Enum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLUME = "volume"


@dataclass
class IndicatorVote:
    """Single indicator's vote within its family."""
    name: str
    vote: Vote
    strength: float  # 0.0–1.0, absolute strength of signal
    family: FamilyName


@dataclass
class FamilyVote:
    """Aggregated vote for one family."""
    family: FamilyName
    vote: Vote
    strength: float  # -1.0 (strong sell) to +1.0 (strong buy), normalized
    indicators_count: int
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    detail: list[str] = field(default_factory=list)

    @property
    def weight(self) -> float:
        """Evidence-based weight from academic research (see FamilyWeights.WEIGHTS)."""
        return FamilyWeights.WEIGHTS[self.family]


@dataclass
class DirectionResult:
    """Final direction score from family consensus."""
    score: float  # 0.0 (strong sell) to 1.0 (strong buy), normalized
    family_votes: list[FamilyVote]
    dominant_family: FamilyName | None = None
    consensus: float = 0.0  # 0.0 (no agreement) to 1.0 (unanimous)

    @property
    def direction(self) -> Vote:
        if self.score > 0.7:
            return Vote.BUY
        elif self.score < 0.3:
            return Vote.SELL
        return Vote.HOLD


class FamilyWeights:
    """Immutable weights for each family."""
    WEIGHTS: ClassVar[dict[FamilyName, float]] = {
        FamilyName.MOMENTUM: 0.50,  # ↑ from 0.35 — strongest academic evidence (time-series momentum)
        FamilyName.TREND: 0.35,     # maintained — good evidence but regime-dependent
        FamilyName.VOLUME: 0.15,    # ↓ from 0.30 — weak academic backing for directional prediction
    }


# Mapping from indicator name to family
INDICATOR_FAMILY: dict[str, FamilyName] = {
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
    "Supertrend": FamilyName.TREND,
    "Ichimoku": FamilyName.TREND,
    "AutoTrend": FamilyName.TREND,
    "RubeGoldberg": FamilyName.TREND,
    "Parabolic SAR": FamilyName.TREND,
    "EchoChamber": FamilyName.TREND,
    "MatchFinder": FamilyName.TREND,
    "UnFairValueGap": FamilyName.TREND,
    # Composites — Momentum family
    "FearGreed": FamilyName.MOMENTUM,
    "Chimera": FamilyName.MOMENTUM,
    # Pattern indicators — TREND family
    "DoubleTopBottom": FamilyName.TREND,
    "HeadShoulders": FamilyName.TREND,
    "WedgePattern": FamilyName.TREND,
    "AutoFractal": FamilyName.TREND,
    "CupAndHandle": FamilyName.TREND,
    "PatternDetectorCombined": FamilyName.TREND,
    # Volume (3)
    "OBV": FamilyName.VOLUME,
    "MFI": FamilyName.VOLUME,
    "VWAPIndicator": FamilyName.VOLUME,
}


def _vote_from_signal(signal: str | None) -> Vote:
    """Convert a signal string to a Vote enum."""
    if signal is None:
        return Vote.HOLD
    s = signal.upper()
    if s in ("BUY", "BULLISH"):
        return Vote.BUY
    elif s in ("SELL", "BEARISH"):
        return Vote.SELL
    return Vote.HOLD


def compute_family_votes(indicator_votes: list[IndicatorVote]) -> list[FamilyVote]:
    """Compute family-level consensus votes from individual indicator votes.

    Within each family:
      - Count BUY, HOLD, SELL votes
      - Majority determines family vote direction
      - Strength = (buy - sell) / total, normalized to [-1, 1]

    Between families:
      - Weighted average determines DirectionResult
    """
    # Group by family
    families: dict[FamilyName, list[IndicatorVote]] = {
        FamilyName.TREND: [],
        FamilyName.MOMENTUM: [],
        FamilyName.VOLUME: [],
    }
    for iv in indicator_votes:
        families[iv.family].append(iv)

    family_votes: list[FamilyVote] = []
    for family, votes in families.items():
        if not votes:
            continue

        buy_count = sum(1 for v in votes if v.vote == Vote.BUY)
        hold_count = sum(1 for v in votes if v.vote == Vote.HOLD)
        sell_count = sum(1 for v in votes if v.vote == Vote.SELL)
        total = len(votes)

        # Normalized strength: (buy - sell) / total → [-1, 1]
        raw_strength = (buy_count - sell_count) / total

        # Determine family vote (majority rules)
        if buy_count > sell_count and buy_count >= 2:
            family_vote = Vote.BUY
        elif sell_count > buy_count and sell_count >= 2:
            family_vote = Vote.SELL
        else:
            family_vote = Vote.HOLD

        # Detail: which indicators contributed what
        detail = [f"{v.name}:{v.vote.value}({v.strength:.2f})" for v in votes]

        fv = FamilyVote(
            family=family,
            vote=family_vote,
            strength=raw_strength,
            indicators_count=total,
            buy_count=buy_count,
            hold_count=hold_count,
            sell_count=sell_count,
            detail=detail,
        )
        family_votes.append(fv)

    return family_votes


def compute_direction_score(family_votes: list[FamilyVote]) -> DirectionResult:
    """Compute final direction score from family consensus votes.

    Direction score = weighted average of family strengths
    Normalized from [-1, 1] → [0, 1]
    """
    if not family_votes:
        return DirectionResult(score=0.5, family_votes=[])

    weighted_sum = sum(
        fv.strength * fv.weight for fv in family_votes
    )
    total_weight = sum(fv.weight for fv in family_votes)

    if total_weight == 0:
        return DirectionResult(score=0.5, family_votes=family_votes)

    raw_score = weighted_sum / total_weight  # [-1, 1]
    direction_score = (raw_score + 1.0) / 2.0  # normalize to [0, 1]

    # Dominant family: the one with highest absolute strength
    dominant = max(family_votes, key=lambda fv: abs(fv.strength)) if family_votes else None

    # Consensus: how many families agree?
    if family_votes:
        votes = [fv.vote for fv in family_votes]
        if all(v == Vote.BUY for v in votes):
            consensus = 1.0
        elif all(v == Vote.SELL for v in votes):
            consensus = 1.0
        elif all(v == Vote.HOLD for v in votes):
            consensus = 1.0
        elif len(set(votes)) == 1:
            consensus = 1.0
        else:
            # Mixed — lowest absolute family vote = consensus measure
            consensus = min(abs(fv.strength) for fv in family_votes)
    else:
        consensus = 0.0

    return DirectionResult(
        score=direction_score,
        family_votes=family_votes,
        dominant_family=dominant.family if dominant else None,
        consensus=consensus,
    )


def combine(indicator_votes: list[IndicatorVote]) -> tuple[DirectionResult, list[FamilyVote]]:
    """Full pipeline: individual votes → family consensus → direction score.

    Returns (DirectionResult, list[FamilyVote]).
    """
    family_votes = compute_family_votes(indicator_votes)
    direction = compute_direction_score(family_votes)
    return direction, family_votes
