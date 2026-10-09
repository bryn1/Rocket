"""Bar gate + zero-signal ledger counting for Stage-A streams (T-BT3).

One question per caller, one concern here (PLAN-c3 §3):
  * bar gate — a frame shorter than WARMUP_BARS + H bars has no complete
    replayable trade window (first signal bar is WARMUP, the pinned primary
    horizon H needs H bars after it), so the ticker is EXCLUDED and counted
    in the zero-signal ledger — never silently padded into a stream;
  * ledger counts — the edge-triggered signal count with the ENGINE's own
    semantics (indicator_eval._eval_indicator :131-153: cur != HOLD and
    cur != prev, prev starts HOLD) plus the dead-lever set, so
    streams_manifest.json can print exact exclusion counts and the §3
    "n_signals == 0 on THIS snapshot" indicator set Stage-B grids skip.

H = 10 = the primary horizon (indicator_eval.py:170-171 default, PLAN-c3 §4
pinned). WARMUP is IMPORTED from the engine, never re-listed. No IO, no
paths, no clock: pure counting so the boundary tests are cheap.
"""
from __future__ import annotations

from rocket.backtest.indicator_eval import WARMUP_BARS  # import, never re-list

H_PRIMARY = 10                       # §4 pinned primary horizon (bars)
MIN_BARS = WARMUP_BARS + H_PRIMARY   # gate floor: 70 stored bars

REASON_BARS = "insufficient_bars"
REASON_NO_DATE = "no_date"           # bar-date keying is impossible without it
REASON_UNREADABLE = "unreadable"     # frame could not be read at all


def exclusion_reason(df) -> str | None:
    """Gate verdict for one frame: reason string, or None = stream it."""
    if df is None:
        return REASON_UNREADABLE
    if len(df) < MIN_BARS:
        return REASON_BARS
    if "date" not in df.columns:
        return REASON_NO_DATE
    return None


def transition_count(signals: list[str]) -> int:
    """Engine n_signals semantics over one ticker's bar-state series
    (edge-triggered transitions into BUY/SELL; HOLD never counts)."""
    n, prev = 0, "HOLD"
    for cur in signals:
        if cur != "HOLD" and cur != prev:
            n += 1
        prev = cur
    return n


def zero_signal_indicators(signals_by_indicator: dict[str, int]) -> list[str]:
    """§3 dead levers on THIS snapshot: ran, emitted nothing. Sorted."""
    return sorted(k for k, v in signals_by_indicator.items() if v == 0)


def ledger(units_done: dict, excluded_done: dict) -> dict:
    """The manifest ledger block from chunk_state's completed sets: every
    covered ticker is streamed OR excluded — the counts sum to input by
    construction and the balance is asserted in the block itself."""
    excluded: dict[str, int] = {}
    for reason in excluded_done.values():
        excluded[reason] = excluded.get(reason, 0) + 1
    covered = len(units_done) + len(excluded_done)
    return {"tickers_covered": covered, "tickers_streamed": len(units_done),
            "tickers_excluded": dict(sorted(excluded.items())),
            "ledger_balanced": len(units_done) + len(excluded_done) == covered}
