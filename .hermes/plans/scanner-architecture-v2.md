# Rocket Scanner v2 — Architecture Plan

> **Goal**: Transform the flat 20-indicator voting system into a modular, statistically-meaningful scoring pipeline with regime detection, hysteresis, risk-aware scoring, and calibrated confidence.
>
> **Constraint**: 50–70K token context slot. All changes must be modular (one concern per file). Crew dispatches are detached; integration is sequential.

---

## Part 1: Indicator Audit — What to Keep & What to Kill

### The Core Insight
20 indicators → but only **~6 independent signals** are actually measured. The rest are correlated copies. The new system must count **independent sources**, not raw indicator votes.

### Indicator Pruning Decision Tree

```
┌─ TREND FAMILY (price above/below moving avg) ─────────────┐
│  EMA9, EMA21, EMA50, EMA200   ← ALL REMOVE               │
│  EMACrossover (9/21)          ← KEEP (crossover = unique) │
│  ADX                        → KEEP (trend strength)       │
│  Ichimoku                   → KEEP (multi-layer trend)    │
│  Supertrend                 → KEEP (trend direction)      │
│  Parabolic SAR              → KILL (same as Supertrend)   │
│  → RESULT: 3 unique trend sources (crossover, ADX, cloud) │
└────────────────────────────────────────────────────────────┘

┌─ MOMENTUM FAMILY (rate of change / overbought) ──────────┐
│  RSI                      → KEEP (mean reversion)         │
│  MACD                     → KEEP (momentum shift)         │
│  Stochastic               → KILL (same as RSI)            │
│  Williams %R              → KILL (same as Stochastic)     │
│  ROC                      → KEEP (pure momentum)          │
│  CCI                      → KILL (same as Bollinger)      │
│  → RESULT: 3 unique momentum sources                     │
└────────────────────────────────────────────────────────────┘

┌─ VOLATILITY FAMILY (price range expansion/contraction) ────┐
│  Bollinger Bands          → KEEP (mean reversion zone)    │
│  ATR                      → KEEP (absolute volatility)    │
│  Donchian Channel         → KILL (same direction as BB)   │
│  → RESULT: 2 unique volatility sources                    │
└────────────────────────────────────────────────────────────┘

┌─ VOLUME FAMILY ────────────────────────────────────────────┐
│  OBV                      → KEEP (cumulative flow)        │
│  MFI                      → KEEP (volume-weighted RSI)    │
│  VWAP                     → KEEP (institutional level)    │
│  → RESULT: 3 unique volume sources                        │
└────────────────────────────────────────────────────────────┘

TOTAL: 20 → 11 INDICATORS (45% reduction, same signal coverage)
```

### Why Each Kill is Safe

| Indicator | Killed By | Correlation | Rationale |
|-----------|-----------|-------------|-----------|
| EMA9/21/50/200 | EMACrossover | 0.99+ | All measure "price vs MA"; crossover captures the dynamic version |
| Parabolic SAR | Supertrend | 0.95+ | Both are trailing stop indicators; Supertrend uses ATR for adaptability |
| Stochastic | RSI | 0.92+ | Both measure overbought/oversold on normalized scale |
| Williams %R | Stochastic | 0.99+ | Mathematical transformation of the same calculation |
| CCI | Bollinger Bands | 0.95+ | CCI = (price - SMA) / (0.015 * MAD); BB = SMA ± 2σ. Same primitive. |
| Donchian Channel | Bollinger Bands | 0.85+ | Both measure "where is price in range"; BB adds volatility context |

### Final Indicator List (11)

```python
# TREND (3)
EMACrossover(fast=9, slow=21)    # Dynamic cross signal
ADX(period=14)                    # Trend strength (not direction)
IchimokuCloud()                   # Multi-layer trend analysis

# MOMENTUM (3)
RSI(period=14)                    # Mean reversion zone
MACD(fast=12, slow=26, signal=9) # Momentum shift detection
ROC(period=10)                    # Pure rate of change

# VOLATILITY (2) — RISK only, not direction
BollingerBands(period=20, std=2)  # Position in volatility range
ATR(period=14)                    # Absolute volatility for risk

# VOLUME (3)
OBV(period=20)                    # Cumulative volume flow
MFI(period=14)                    # Volume-weighted sentiment
VWAPIndicator()                   # Institutional reference
```

---

## Part 2: New Architecture — 5-Layer Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    MARKET REGIME DETECTION                      │
│  Input: S&P500 / OMX30 / SHCOMP trend + VIX-like volatility   │
│  Output: Regime enum (BULL / BEAR / CHOP) + regime_score       │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DIRECTION SCORE (0→1)                        │
│  Inputs: Trend (3) + Momentum (3) + Volume (3)                 │
│  Method: Family consensus → weighted average                   │
│  Each family votes (majority rules), then families are weighted│
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CONFIDENCE SCORE (0→1)                       │
│  Inputs: Agreement across families + signal strength +         │
│           multi-timeframe alignment                             │
│  High confidence = all families agree + strong signals         │
│  Low confidence = mixed families + weak signals                │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                      RISK SCORE (0→1)                           │
│  Inputs: ATR% + Bollinger squeeze + volume anomaly             │
│  Output: Risk multiplier (1.0 = normal, 0.5 = high risk)      │
│  Applied to: Confidence score only (NOT direction)             │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FINAL SIGNAL + HYSTERESIS                     │
│  Input: Direction × Confidence × Risk × Regime_filter          │
│  Hysteresis: BUY→SELL needs stronger evidence than new BUY     │
│  Output: (Signal, FinalScore, Strength, Reason)               │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Details

#### Layer 0: Market Regime Detection
**New file**: `rocket/technical/regime.py`

- **Bull**: S&P500 (or equivalent index) above EMA50 AND EMA200, ADX > 20
- **Bear**: Index below EMA50, or VIX > 30
- **Chop**: ADX < 15 (no clear trend in any index)
- **Index mapping**:
  - USA → SPY (`^SPY`)
  - Sweden → OMX30 (`^OMXS30`)
  - China → SHCOMP (`000001.SS`)
  - India → NIFTY (`^NSEI`)

**Impact**: In BEAR regime, BUY signals get 0.7× multiplier. In CHOP regime, all signals get 0.5×.

#### Layer 1: Direction Score
**Replaces**: `signal_combiner.py` voting logic

- **Family voting**: Each family (Trend, Momentum, Volume) votes internally
  - If 2/3 indicators in family say BUY → family votes BUY
  - If 2/3 say HOLD → family votes HOLD
  - If 2/3 say SELL → family votes SELL
- **Family weights**:
  - Trend: 35%
  - Momentum: 35%
  - Volume: 30%
- **Direction Score** = weighted sum of family votes
  - All 3 families BUY → score = 1.0
  - 2 BUY + 1 HOLD → score = 0.7
  - 1 BUY + 2 HOLD → score = 0.3
  - Mixed → score ≈ 0.0

#### Layer 2: Confidence Score
**New file**: `rocket/scoring/confidence.py`

Factors (all normalized 0→1):
1. **Family agreement**: How many families agree? (0–3)
   - 3 agree → 1.0, 2 agree → 0.6, 1 → 0.2
2. **Signal strength**: Average absolute signal within families
   - Strong signals → 1.0, weak → 0.3
3. **Volume confirmation**: Volume indicators align with price direction
   - Price up + volume up → 1.0, divergent → 0.2

```
confidence = 0.5 × family_agreement + 0.3 × signal_strength + 0.2 × volume_confirmation
```

#### Layer 3: Risk Score
**Modified**: Move ATR + Bollinger from signal_combiner to new `rocket/scoring/risk.py`

- **ATR%**: `atr / close × 100`
  - < 2% → risk = 0.0 (very low)
  - 2–5% → risk = 0.5 (normal)
  - > 5% → risk = 1.0 (high)
- **Bollinger squeeze**: `(upper - lower) / sma`
  - < 0.05 → squeeze (potential explosion) → risk = 0.8
  - > 0.15 → wide → risk = 0.2
- **Volume anomaly**: current volume / avg volume
  - > 3× → anomaly → risk = 0.7
- **Risk multiplier** = `1.0 - risk_score × 0.5`
  - Low risk → multiplier = 1.0 (full confidence)
  - High risk → multiplier = 0.5 (halved confidence)

**Critical rule**: Risk only modifies confidence. Direction score is pure technical signal, unaffected by risk.

#### Layer 4: Signal + Hysteresis
**Replaces**: `signal_combiner.py` + `nightly_scan.py` signal derivation

**Hysteresis thresholds** (different for entry vs exit):

| Transition | Required Score | Required Confidence |
|------------|---------------|-------------------|
| HOLD → BUY | > 0.7 | > 0.6 |
| BUY → HOLD | < 0.4 | < 0.4 |
| HOLD → SELL | < -0.7 | > 0.6 |
| SELL → HOLD | > -0.4 | < 0.4 |
| BUY → SELL | < -0.6 | > 0.8 (harder!) |
| SELL → BUY | > 0.6 | > 0.8 (harder!) |

**Final score calculation**:
```
final_score = direction_score × confidence_multiplier × risk_multiplier × regime_multiplier
```

**Strength levels** (for Telegram display):
- **Strong Bullish**: score > 0.75 + confidence > 0.7
- **Bullish**: score > 0.5 + confidence > 0.5
- **Moderate**: 0.3 < score < 0.5 or confidence < 0.5
- **Weak**: score < 0.3

---

## Part 3: File Structure (New)

```
rocket/
├── technical/
│   ├── models.py              ← KEEP (Signal, SignalCategory, IndicatorResult)
│   ├── base.py                ← KEEP (BaseIndicator, normalize_score)
│   ├── regime.py              ← NEW: Market regime detection
│   ├── families.py            ← NEW: Family consensus voting
│   ├── momentum.py            ← MODIFIED: Remove Stochastic, WilliamsR, CCI
│   ├── trend.py               ← MODIFIED: Remove EMA9, EMA21, EMA50, EMA200, ADX stays
│   ├── volatility.py          ← MODIFIED: Remove Donchian, ATR used only for risk
│   ├── volume.py              ← KEEP (all 3 are unique)
│   ├── advanced.py            ← MODIFIED: Remove ParabolicSAR
│   └── signal_combiner.py     ← DELETE: replaced by families.py + risk.py
├── scoring/
│   ├── models.py              ← KEEP + EXTEND: add DirectionScore, ConfidenceScore, RiskScore
│   ├── weighter.py            ← DELETE: replaced by new pipeline
│   ├── rocket_score.py        ← REWRITE: uses new pipeline
│   ├── confidence.py          ← NEW: confidence calculation
│   ├── risk.py                ← NEW: risk calculation
│   └── filter.py              ← KEEP (quality filters)
├── scan_engine/
│   ├── engine.py              ← MODIFIED: use new scoring
│   ├── models.py              ← KEEP + EXTEND: hysteresis state
│   └── storage.py             ← KEEP (no changes needed)
├── backtest/
│   ├── engine.py              ← MODIFIED: walk-forward, costs, slippage
│   ├── strategy.py            ← NEW: configurable strategy with thresholds
│   └── sensitivity.py         ← KEEP (parameter sweep)
├── notifications.py           ← NEW: cooldown, confidence gating
├── nightly_scan.py            ← MODIFIED: use new pipeline
└── data/
    ├── universe.py            ← KEEP
    ├── fetcher.py             ← KEEP (+ index data fetching)
    └── models.py              ← KEEP
```

---

## Part 4: Implementation Order (Sequential)

Each step is a **detached dispatch to Teddy** → **Artemis gate** → **Integrate**.

### Step 1: Indicator Cleanup (trend.py, momentum.py, volatility.py, advanced.py)
- Remove 9 indicators (EMAs, Stochastic, WilliamsR, CCI, Donchian, ParabolicSAR)
- Keep ATR but change its role from "signal" to "risk input"
- Keep Bollinger but store raw values for risk calculation
- Update `__init__.py` exports

### Step 2: Regime Detection (new `regime.py`)
- Implement `detect_regime(index_df)` → `Regime.BULL/BEAR/CHOP`
- Fetch index data via yfinance (SPY, OMXS30, SHCOMP, NIFTY)
- Output: `RegimeResult(regime, score, index_trend)`

### Step 3: Family Consensus (new `families.py`)
- Implement `FamilyVote` dataclass (family name, vote, indicators_count)
- Implement `compute_family_votes(indicators: list[IndicatorResult])` → `list[FamilyVote]`
- Map each indicator to its family
- Family votes: majority rules within each family

### Step 4: Risk Module (new `risk.py`)
- Implement `compute_risk(atr_pct, bb_position, bb_width, volume_ratio)` → `RiskResult`
- Output: risk_score (0→1), risk_multiplier (0.5→1.0)
- No signal/direction logic — risk ONLY

### Step 5: Confidence Module (new `confidence.py`)
- Implement `compute_confidence(family_votes, signal_strengths)` → `ConfidenceResult`
- Output: confidence_score (0→1)

### Step 6: Rocket Score Rebuild (`rocket_score.py`)
- Replace `compute_rocket_score()` with new pipeline:
  ```python
  regime = detect_regime(index_df)
  families = compute_family_votes(indicators)
  risk = compute_risk(...)
  confidence = compute_confidence(families, ...)
  direction = weighted_family_score(families)
  final = direction × confidence × risk_multiplier × regime_multiplier
  signal = apply_hysteresis(final, prev_signal)
  ```
- Keep `SignalSummary` for backward compatibility with storage

### Step 7: Hysteresis in Signal Engine (`scan_engine/models.py`, `engine.py`)
- Add `prev_signal` tracking per ticker
- Apply hysteresis thresholds in signal derivation
- Store hysteresis state in SignalState

### Step 8: Backtest Improvement (`backtest/engine.py`)
- Add transaction costs (0.1% per trade)
- Add slippage (0.05% per trade)
- Implement walk-forward validation (train 60d, test 30d)
- Add baseline comparison (buy-and-hold, random)

### Step 9: Notifications (`notifications.py`)
- Add cooldown: max 1 signal per ticker per 24h
- Add confidence gating: only notify if confidence > 0.5
- Add signal strength filtering (only Strong/Moderate)

### Step 10: Integration & Testing
- Run full scan on 1 region (USA) with test mode
- Verify all 11 indicators compute correctly
- Verify regime detection works for all 4 regions
- Verify backtest runs with new signals
- End-to-end test: scan → store → notify

---

## Part 5: Backtest Calibration Plan

After the new architecture is built, run calibration:

1. **Scan the same 100 tickers** (50 USA, 25 Sweden, 15 China, 10 India) with the new system
2. **Run 90-day backtest** with walk-forward (train 60d / test 30d, repeated 3×)
3. **Measure**:
   - Win rate by signal strength (Strong vs Moderate)
   - Average return by regime (bull/bear/chop)
   - Sharpe ratio with costs
   - Max drawdown
4. **Calibrate thresholds**:
   - If win rate < 55% → increase confidence threshold
   - If too few signals (< 5/day) → decrease score threshold
   - If Sharpe < 0.5 → increase hysteresis gaps

---

## Part 6: Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| 11 indicators too few | Start with 11; can re-add Donchian as "breakout" signal if backtest shows benefit |
| Regime detection wrong for non-US | Use regional indices: OMXS30 for Sweden, SHCOMP for China, NIFTY for India |
| Family consensus too binary | Use weighted family voting (not just majority) |
| Hysteresis blocks valid signals | Keep hysteresis gaps reasonable; review weekly |
| New architecture breaks existing DB schema | SignalState schema unchanged; only score derivation changes |
| Backtest overfitting | Use walk-forward, not full-period optimization. Test on out-of-sample data. |

---

## Acceptance Criteria

1. ✅ **11 indicators** instead of 20, each measuring a unique primitive
2. ✅ **5-layer pipeline** (regime → direction → confidence → risk → signal)
3. ✅ **Hysteresis** prevents flip-flopping (BUY→SELL requires stronger signal than BUY)
4. ✅ **Volatility affects risk only**, not direction score
5. ✅ **Backtest includes costs, slippage, walk-forward**
6. ✅ **Notifications have cooldown + confidence gating**
7. ✅ **All 4 regions work** (USA, Sweden, China, India)
8. ✅ **End-to-end test passes**: scan → store → notify → backtest

---

## Notes for Crew Dispatches

- **Teddy**: code authoring only. Each subtask = one concern. Never ask Teddy to verify.
- **Artemis**: adversarial gate after each Teddy dispatch. Read the files, verify correctness.
- **Svarkor (me)**: integration — wire modules together, run the full scan, verify end-to-end.
- **Model**: All crew use the shared 35B pool. Keep concurrent dispatches ≤ 3.
- **Version control**: Every change committed with message. Push to `bryn1/rocket`.
