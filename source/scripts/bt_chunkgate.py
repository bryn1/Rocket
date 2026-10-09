"""Chunk-start gate + resumable chunk_state for the campaign's streamer (T-BT3).

Two halves of one concern — "may this chunk start, and where does it resume"
(PLAN-c3 §5):
  * gate — a chunk starts only if the store still reproduces the snapshot's
    listing sha (bt_snapshot.validate_snapshot REUSED as the checker — no
    second validator beside it) OR the UTC clock is at/after 07:00 (the
    nightly fetch, 05:00-05:15, is finished and nobody else is waiting on
    the box). Absent/unreadable evidence is never "green" (§2). A drifted
    store at >= 07:00 may start by the window rule; streams carry the
    SNAPSHOT's store_snapshot_ts regardless, so misattribution stays
    visible (the final campaign run is separately gated on a GREEN nightly
    manifest, §6 T-BT7).
  * chunk_state.json — the completed (ticker, indicator) key set + ledger
    totals; --resume skips them. Corrupt/incompatible/foreign state REFUSES
    with the named recovery (move or delete the file), never a silent
    restart. All writes go tmp + os.replace: a crash mid-write can never
    corrupt the file that drives the next resume.
Courtesy nice(19) + ionice idle lives here too — best-effort, warn, no fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import bt_snapshot                    # noqa: E402 (gate validator REUSED)

GATE_HOUR_UTC = 7                    # §5: the window opens 07:00 UTC
STATE_VERSION = 1


class GateRefused(RuntimeError):
    """Chunk start refused; .reasons names every violated clause (§5)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class ChunkStateError(RuntimeError):
    """chunk_state.json corrupt/foreign — refuse with the named recovery."""


def courtesy() -> None:
    """Best-effort nice(19) + ionice idle at entry: warn, never fail (§5)."""
    try:
        os.nice(19)
    except OSError as exc:
        print(f"warn: nice(19) refused: {exc}", file=sys.stderr)
    try:
        subprocess.run(["ionice", "-c3", "-p", str(os.getpid())],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warn: ionice idle refused: {exc}", file=sys.stderr)


def chunk_start_gate(snapshot_dir: Path, store_dir: Path,
                     now: datetime) -> dict:
    """(a) store reproduces the snapshot listing sha -> free to start at any
    hour; (b) clock >= 07:00 UTC -> window courtesy; else REFUSE naming both
    clauses. bt_snapshot.validate_snapshot is THE checker (no second one)."""
    drift = bt_snapshot.validate_snapshot(store_dir, snapshot_dir)
    if not drift:
        return {"mode": "store-matches-snapshot"}
    if now >= now.replace(hour=GATE_HOUR_UTC, minute=0, second=0,
                          microsecond=0):
        return {"mode": "courtesy-window", "drift": drift}
    raise GateRefused([f"{r} (store drifted from snapshot)" for r in drift]
                      + [f"clock {now:%H:%M} UTC < "
                         f"{GATE_HOUR_UTC:02d}:00: the courtesy window is "
                         "closed — a drifted store may only start a chunk "
                         "at/after 07:00 UTC"])


def fresh_state(expect_sha: str) -> dict:
    return {"version": STATE_VERSION, "store_listing_sha256": expect_sha,
            "units_done": {}, "excluded_done": {},
            "signals_by_indicator": {}, "bytes_written": 0}


def load_state(path: Path, expect_sha: str, resume: bool) -> dict:
    """[]-state on first run; with --resume validates; without it refuses
    over an existing state. Recovery is always NAMED, never a silent wipe."""
    if not path.exists():
        return fresh_state(expect_sha)
    if not resume:
        raise ChunkStateError(
            f"{path} exists: pass --resume to continue it, or move/delete "
            "it to restart this snapshot's streams from scratch")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["version"] == STATE_VERSION and \
            doc["store_listing_sha256"] == expect_sha and \
            isinstance(doc["units_done"], dict)
    except (ValueError, KeyError, AssertionError) as exc:
        raise ChunkStateError(
            f"corrupt/incompatible chunk_state {path} ({exc!r}): move or "
            "delete it to restart this snapshot's streams from scratch") from exc
    return doc


def save_json(path: Path, doc: dict) -> None:
    """Atomic, byte-deterministic (sorted keys, indent 2)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
