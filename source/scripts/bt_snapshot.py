"""Campaign snapshot builder + validator: the FROZEN-cuts manifest (T-BT2).

PLAN-c3 §2/§4: one campaign snapshot = the store listing pinned by
``store_listing_sha256`` (sha over sorted ``"name,last_bar"`` of every CSV),
its ``data_last_bar``, and the cuts C1/C2/C3 **computed ONCE at build from
that snapshot's data_last_bar and never re-derived** (the store grows nightly
— recomputation would drift C3, DA c1 P1-1). ``cuts.fold_day_counts`` are the
ACTUAL stored trading days inside each window, measured at build; the §4
floors (VAL folds >= 55, TRAIN >= 60 = WARMUP_BARS, holdout >= 30) are BUILD
FAILURES, never warnings and never an automatic fold shrink.

Cut geometry (§4 build rule, §7.3 rec): calendar dates, applied panel-wide on
the stored naive dates — C3 = data_last_bar - 2 months; C2 = C3 - 3 months;
C1 = C2 - 3 months; windows TRAIN <= C1 < VAL-1 <= C2 < VAL-2 <= C3 < HOLDOUT.
No stream/slice/grid code here (T-BT3/T-BT4 own those). Zero network, EVER.

Manifest idiom: modeled on generate_demo_page._write_manifest (:199-203) and
its flat dict (:299-313) — a json.dumps(indent=2) into the campaign's .tmp/.
generate_demo_page is deliberately NOT imported: what transfers is the idiom,
not the demo pipeline (importing it drags fetch/render into a data tool).
Write-once: an existing manifest is never overwritten (frozen cuts); validate
recomputes the listing sha, the frozen cuts and the day counts, and REJECTS a
tampered sha, drifted cuts or a store that no longer reproduces the snapshot.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_io                                  # noqa: E402 (read seams, §6 reuse)
from full_universe import CACHE_DIR, load_plan   # noqa: E402

CAMPAIGN_DIR = _REPO / ".audits" / "20261009-backtest0"
HOLDOUT_MONTHS, VAL_MONTHS = 2, 3                # §4 build rule (C3/C2/C1)
FLOOR_TRAIN, FLOOR_VAL, FLOOR_HOLDOUT = 60, 55, 30   # §4 floors: FAIL, not warn
MANIFEST_RELPATH = (".tmp", "backtest_manifest.json")


class SnapshotError(RuntimeError):
    """Build refused; .reasons lists every violated floor/rule (fail-closed).
    Nothing is written when this is raised."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def minus_months(d: date, months: int) -> date:
    """Calendar-month subtraction; day clamps to the target month's end."""
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def compute_cuts(data_last_bar: str) -> dict:
    """Frozen C1/C2/C3 from the snapshot's own data_last_bar (pure)."""
    last = date.fromisoformat(data_last_bar)
    c3 = minus_months(last, HOLDOUT_MONTHS)
    c2 = minus_months(c3, VAL_MONTHS)
    c1 = minus_months(c2, VAL_MONTHS)
    return {"C1": c1.isoformat(), "C2": c2.isoformat(), "C3": c3.isoformat()}


def fold_day_counts(dates: set[str], cuts: dict) -> dict:
    """Stored trading days per window (ISO dates compare as strings)."""
    c1, c2, c3 = cuts["C1"], cuts["C2"], cuts["C3"]
    return {"train": sum(d <= c1 for d in dates),
            "val1": sum(c1 < d <= c2 for d in dates),
            "val2": sum(c2 < d <= c3 for d in dates),
            "holdout": sum(d > c3 for d in dates)}


def floor_failures(counts: dict) -> list[str]:
    """§4: a short fold FAILS the build — each failure names its floor."""
    out = []
    if counts["val1"] < FLOOR_VAL:
        out.append(f"fold floor: val1 {counts['val1']} < {FLOOR_VAL} days")
    if counts["val2"] < FLOOR_VAL:
        out.append(f"fold floor: val2 {counts['val2']} < {FLOOR_VAL} days")
    if counts["train"] < FLOOR_TRAIN:
        out.append(f"fold floor: train {counts['train']} < {FLOOR_TRAIN} days")
    if counts["holdout"] < FLOOR_HOLDOUT:
        out.append(f"fold floor: holdout {counts['holdout']} < "
                   f"{FLOOR_HOLDOUT} days")
    return out


def scan_store(store_dir: Path) -> tuple[dict[str, str], set[str]]:
    """One pass over every CSV: {name: last_bar} + union of stored dates.
    Unreadable/undated files stay in the listing with last_bar '' — the sha
    pins them too (the same bytes must reproduce the same snapshot)."""
    listing: dict[str, str] = {}
    dates: set[str] = set()
    for path in sorted(store_dir.glob("*.csv")):
        try:
            df = store_io.read_cache(path)
        except Exception:                                     # corrupt -> ''
            listing[path.name] = ""
            continue
        listing[path.name] = store_io.last_bar_date(df)
        if "date" in df.columns and len(df):
            dates.update(str(d)[:10] for d in
                         pd.to_datetime(df["date"]).dropna())
    return listing, dates


def listing_sha256(listing: dict[str, str]) -> str:
    """sha256 over sorted ``"name,last_bar"`` rows (PLAN-c3 §2, pinned form)."""
    blob = "\n".join(f"{name},{last}" for name, last in sorted(listing.items()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fixture_sha(store_dir: Path) -> str | None:
    """sha256 of the store dir's SHA256SUMS (fixtures carry one, golden35
    idiom); the live store has none -> None. Identifies WHICH fixture."""
    sums = store_dir / "SHA256SUMS"
    return hashlib.sha256(sums.read_bytes()).hexdigest() if sums.exists() else None


def _git_sha() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO,
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def manifest_path(out_dir: Path) -> Path:
    return out_dir.joinpath(*MANIFEST_RELPATH)


def build_snapshot(store_dir: Path, out_dir: Path, *,
                   run_iso: str | None = None) -> dict:
    """Freeze one campaign snapshot; returns the written manifest dict.
    Raises SnapshotError (writing NOTHING) on an empty store, a §4 fold floor,
    or an existing manifest (write-once: frozen cuts are never replaced)."""
    store_dir, out_dir = Path(store_dir), Path(out_dir)
    listing, dates = scan_store(store_dir)
    if not listing:
        raise SnapshotError([f"no CSVs in store dir: {store_dir}"])
    data_last_bar = store_io.data_last_bar(listing.values())
    if not data_last_bar:
        raise SnapshotError([f"no stored dates under {store_dir}"])
    cuts = compute_cuts(data_last_bar)
    counts = fold_day_counts(dates, cuts)
    failures = floor_failures(counts)
    path = manifest_path(out_dir)
    if path.exists():
        failures.append(f"write-once: manifest exists: {path}")
    if failures:
        raise SnapshotError(failures)
    plan = load_plan()                             # registry facts, imported
    manifest = {
        "run_iso": run_iso or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_sha(),
        "store_n": len(listing),
        "store_listing_sha256": listing_sha256(listing),
        "data_last_bar": data_last_bar,
        "registry_ts": plan.registry_ts,
        "fixture_sha": fixture_sha(store_dir),
        "params": {"holdout_months": HOLDOUT_MONTHS, "val_months": VAL_MONTHS,
                   "floors": {"train": FLOOR_TRAIN, "val": FLOOR_VAL,
                              "holdout": FLOOR_HOLDOUT}},
        "chunk_state": {},                         # T-BT3 resumable chunks
        "n_trials_path": 0,                        # §4: inits at 0, never retro
        "cuts": {**cuts, "fold_day_counts": counts},   # FROZEN at build, §2
        "holdout_opened": False,                   # bt_holdout.py opens it (T-BT5+)
    }
    path.parent.mkdir(parents=True, exist_ok=True)             # .tmp/ idiom
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def validate_snapshot(store_dir: Path, out_dir: Path) -> list[str]:
    """[] is the only GREEN answer: the store still reproduces this snapshot's
    listing sha, its frozen cuts still reproduce from the stored
    data_last_bar, and the stored day counts still match the frozen ones."""
    path = manifest_path(Path(out_dir))
    if not path.exists():
        return [f"no manifest at {path}"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    listing, dates = scan_store(Path(store_dir))
    violations = []
    if listing_sha256(listing) != doc["store_listing_sha256"]:
        violations.append("store listing sha mismatch (tampered manifest or "
                          "drifted store)")
    cuts = compute_cuts(doc["data_last_bar"])
    if any(doc["cuts"][k] != v for k, v in cuts.items()):
        violations.append("frozen cuts do not reproduce from the stored "
                          f"data_last_bar {doc['data_last_bar']}")
    counts = fold_day_counts(dates, cuts)
    if doc["cuts"]["fold_day_counts"] != counts:
        violations.append(f"fold_day_counts drifted: stored {counts} != "
                          f"manifest {doc['cuts']['fold_day_counts']}")
    return violations


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("build", "validate"))
    ap.add_argument("--store", default=str(CACHE_DIR), type=Path,
                    help="input store dir (CSVs only; default source/data/raw)")
    ap.add_argument("--out-dir", default=str(CAMPAIGN_DIR), type=Path,
                    help="campaign dir; manifest lands in <dir>/.tmp/ "
                         f"(default {CAMPAIGN_DIR.relative_to(_REPO)})")
    args = ap.parse_args()
    try:
        if args.mode == "build":
            m = build_snapshot(args.store, args.out_dir)
            print(f"snapshot frozen: {m['store_n']} CSVs, data_last_bar "
                  f"{m['data_last_bar']}, cuts {m['cuts']['C1']} / "
                  f"{m['cuts']['C2']} / {m['cuts']['C3']}, days "
                  f"{m['cuts']['fold_day_counts']} -> {manifest_path(args.out_dir)}")
            return
        violations = validate_snapshot(args.store, args.out_dir)
    except SnapshotError as exc:
        print(f"FAIL-CLOSED — snapshot build refused: {'; '.join(exc.reasons)}",
              file=sys.stderr)
        sys.exit(1)
    if violations:
        print(f"REJECT — snapshot invalid: {'; '.join(violations)}",
              file=sys.stderr)
        sys.exit(1)
    print(f"OK — store reproduces snapshot {manifest_path(args.out_dir)}")


if __name__ == "__main__":
    main()
