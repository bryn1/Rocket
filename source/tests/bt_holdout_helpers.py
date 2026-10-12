"""Shared fixtures for the bt_holdout test pair (T-BT5.1 + fix-cycle 1):
one-stop grid fixture, manifest/root helpers, the EXACT-literal mutation
harness (shipped source is never edited), and the two plant helpers that
stage the F1 mid-write <= C3 file and the F4 post-C3 residue."""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_combo as gc                               # noqa: E402
import bt_slice                                     # noqa: E402
import bt_snapshot as bs                            # noqa: E402
from bt_fold_fixtures import LIGHT, gz_lines, make_store, ready_snapshot

BAD_TOKENS = ("gate", "pass", "fail", "verdict", "leaderboard", "no-edge")
SIX = ["MACD", "RSI", "ROC", "CCI", "WilliamsR", "Stochastic"]


def ready_grid(tmp, tickers=("AA", "BB", "CC")):
    """store -> snapshot -> streams -> slices -> ONE pass on EACH VAL fold
    (gate-aware top-K reads both; --combos runs need only the ranking one)."""
    store = make_store(tmp, list(tickers))
    snap = ready_snapshot(tmp, store, list(tickers))
    gc.run_pass(snap, "val1", store, LIGHT)
    gc.run_pass(snap, "val2", store, LIGHT)
    return store, snap


def grid_ids(snap: Path, fold: str = "val2") -> list[str]:
    doc = json.loads((snap / "grid" / fold / "trials.json").read_text(
        encoding="utf-8"))
    return [t["combo"] for t in doc["trials"]]


def frozen_doc(tmp):
    doc = tmp / "LEADERBOARD.md"
    doc.write_text("# frozen leaderboard (doc-freeze precondition)\n",
                   encoding="utf-8")
    return doc


def manifest_of(snap: Path) -> dict:
    return json.loads(bs.manifest_path(snap).read_text(encoding="utf-8"))


def write_manifest(snap: Path, man: dict) -> None:
    bs.manifest_path(snap).write_text(json.dumps(man), encoding="utf-8")


def holdout_root(snap: Path) -> Path:
    return snap / "slices" / "holdout"


def holdout_dates(snap: Path, tkr: str, ind: str) -> list[str]:
    p = holdout_root(snap) / tkr / f"{ind}.jsonl.gz"
    return [b["d"] for b in gz_lines(p)[1:]]


def physical_files(snap: Path) -> list[Path]:
    return sorted(holdout_root(snap).glob("*/*.jsonl.gz"))


def mutated_holdout(*subs: tuple[str, str]):
    """Fresh bt_holdout module with EXACT-literal source flips (the mutation
    lives in these test files only — the shipped module is never edited)."""
    src = (SOURCE / "scripts" / "bt_holdout.py").read_text(encoding="utf-8")
    for old, new in subs:
        assert src.count(old) == 1, f"anchor not unique: {old!r}"
        src = src.replace(old, new)
    mod = types.ModuleType("bt_holdout_mutated")
    mod.__dict__["__file__"] = str(SOURCE / "scripts" / "bt_holdout.py")
    exec(compile(src, "bt_holdout_mutated.py", "exec"), mod.__dict__)
    return mod


def scan_vocab(text: str) -> list[str]:
    low = text.lower()
    return [t for t in BAD_TOKENS if t in low]


def run_cli(snap, store, doc, *extra):
    return subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "bt_holdout.py"),
         "--snapshot-dir", str(snap), "--store", str(store),
         "--doc", str(doc), *extra],
        capture_output=True, text=True, timeout=300)


def plant_stale_le_c3(monkeypatch, snap: Path) -> int:
    """Wrap bt_slice._dump so the FIRST ceremony write is followed by a
    PLANTED trainval-span file (bars <= C2) inside the holdout root — the
    F1 attack the open-every-file re-check exists to catch. Returns how many
    distinct bar dates the plant carries (for the day-count tamper that
    keeps the flipped run clear of the day-count clause)."""
    real = bt_slice._dump
    src = snap / "slices" / "trainval" / "AA" / "RSI.jsonl.gz"
    planted: list[int] = []

    def wrapper(path, docs):
        real(path, docs)
        if not planted:
            planted.append(1)
            (path.parent / "STALE.jsonl.gz").write_bytes(src.read_bytes())

    monkeypatch.setattr(bt_slice, "_dump", wrapper)
    return len({b["d"] for b in gz_lines(src)[1:]})


def plant_gt_c3(snap: Path, tkr: str, ind: str, c3: str) -> None:
    """Write <tkr>/<ind> into the holdout root holding the REAL post-C3
    bars — stale RESIDUE from an earlier partial ceremony (F4), physically
    legal but not part of this run."""
    header, bars = bt_slice.read_stream(snap / "streams" / "AA" /
                                        f"{ind}.jsonl.gz")
    kept = [b for b in bars if b["d"] > c3]
    bt_slice._dump(holdout_root(snap) / tkr / f"{ind}.jsonl.gz",
                   [dict(header)] + kept)
