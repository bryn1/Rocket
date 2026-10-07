"""Stage-3 Indikatorer branch (DESIGN §8): weekly redraw vs honest carry.

Cadence: the timer runs nightly; the sample backtest redraws only on
Saturday UTC (weekday()==5) or via --force-backtest. Two DISTINCT carry
states (F5): 'scheduled' (Mon-Fri skip) carries with cadence wording and NO
failure copy; 'failed' (Saturday raised) keeps today's failure note + the
byte-identical carried indicators block. The artifact-age floor (>10 d, >=2
missed Saturdays) is a loud stderr marker only — scoring/publish are NOT
blocked (the in-page cadence line + sample size tell the truth).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import demo_render

SCHEMA_VERSION = 1        # seam C2/C3: unknown schema == absent (§5.3)
AGE_LIMIT_D = 10          # §8 artifact-age floor (>=2 missed Saturdays)


def load_carried(stats_path: Path, reason: str) -> tuple[dict | None, str | None]:
    """Carry-forward of the committed indicator_stats.json: it stays
    byte-identical in its indicators block, embedded + noted per F5."""
    if not stats_path.exists():
        return None, None
    try:
        doc = json.loads(stats_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, f"{demo_render.EMPTY_BACKTEST_TEXT} (tidigare fil oläsbar: {exc})"
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        got = doc.get("generated_at") if isinstance(doc, dict) else None
        return None, (f"{demo_render.EMPTY_BACKTEST_TEXT} "
                      f"(tidigare fil från {got or '?'} har okänd schema)")
    ga = doc.get("generated_at", "?")
    if reason == "scheduled":
        return doc, f"Indikatorer uppdateras veckovis — senaste körning {ga}"
    return doc, (f"Backtest från {ga} bars vidare — denna löpnings backtest "
                 "lyckades inte.")


def check_schema(doc) -> dict:
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        got = doc.get("schema_version") if isinstance(doc, dict) else type(doc)
        raise ValueError(f"backtest artifact schema {got!r} != {SCHEMA_VERSION}")
    return doc


def stage(plan, *, force_backtest: bool, now_dt: datetime, stats_path: Path,
          live_by_region=None) -> tuple[dict | None, str | None, str | None]:
    """Returns (doc, note, failure|None). Weekly-Saturday branch: the T3
    sample_backtest redraw over the SCORED (store-live) tickers; any other
    day carries with the SCHEDULED note (no failure wording — F5)."""
    if not (force_backtest or now_dt.weekday() == 5):
        return (*load_carried(stats_path, "scheduled"), None)
    try:
        import sample_backtest
        doc = check_schema(sample_backtest.run_sample_backtest(
            plan, live_by_region=live_by_region))
        return doc, None, None
    except Exception as exc:  # noqa: BLE001 — backtest never blocks scoring
        doc, note = load_carried(stats_path, "failed")
        return doc, note, str(exc)


def age_check(doc: dict | None, now_dt: datetime) -> None:
    """>AGE_LIMIT_D -> stderr STALE-INDICATOR-STATS age=<n>d (greppable E3
    alert hook); never blocks the run."""
    if not doc or not doc.get("generated_at"):
        return
    try:
        gen = datetime.strptime(doc["generated_at"][:19],
                                "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return
    age = (now_dt - gen).days
    if age > AGE_LIMIT_D:
        print(f"STALE-INDICATOR-STATS age={age}d", file=sys.stderr)
