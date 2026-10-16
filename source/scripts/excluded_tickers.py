# reason: MC 10062.139.22 — owner ruling 2026-10-16 (parent 10786 append #8,
# Q3): a delisted ticker must not hold the nightly publish hostage. One
# small persisted state file; the fail-closed rule is unchanged for every
# ticker not yet on the list (cf. 10219.1.32 purge, 10219.1.35 design).
"""Exclusion list for tickers not fetched on 2+ consecutive nights.

State file source/data/excluded_tickers.json (git-ignored with the store, so
the nightly never dirties a tracked file): {ticker: {first_seen, last_seen,
nights, reason}}. A ticker is EXCLUDED (coverage gate + scored set) once its
nights >= EXCLUDE_AFTER_NIGHTS; first night it only counts. A ticker that
fetches again is dropped from the file. A run that excludes more than
MAX_EXCLUDED_FRACTION of the universe is a batch/fetcher failure, not a
delisting wave -> the guard still fails closed.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "excluded_tickers.json"
EXCLUDE_AFTER_NIGHTS = 2
MAX_EXCLUDED_FRACTION = 0.005
REASON = "not_fetched 2+ consecutive nights (delisted?)"


def load(path: Path | None = None) -> dict:
    try:
        doc = json.loads((path or STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def update(state: dict, not_fetched, today: str) -> dict:
    """New state after tonight: not_fetched tickers count a night (once per
    day; a gap > 1 day restarts at 1), anything else is dropped."""
    yday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    new: dict = {}
    for t in sorted(not_fetched):
        old = state.get(t)
        if isinstance(old, dict) and old.get("last_seen") == today:
            new[t] = old                                   # same-day rerun
        elif isinstance(old, dict) and old.get("last_seen") == yday:
            new[t] = {**old, "last_seen": today,
                      "nights": int(old.get("nights", 1)) + 1}
        else:
            new[t] = {"first_seen": today, "last_seen": today, "nights": 1,
                      "reason": REASON}
    return new


def excluded(state: dict) -> list[str]:
    return sorted(t for t, v in state.items()
                  if int(v.get("nights", 0)) >= EXCLUDE_AFTER_NIGHTS)


def save(state: dict, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def too_many(n_excluded: int, m_unique: int) -> bool:
    return n_excluded > MAX_EXCLUDED_FRACTION * m_unique
