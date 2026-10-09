"""Materialize the campaign snapshot's fold slices (T-BT4, PLAN-c3 §4 holdout).

From <snapshot>/streams/ (built by bt_streams over the store) write, under
<snapshot>/slices/:

  trainval/ = bars with date <= C2 ONLY  (TRAIN + VAL-1 — the grid's SELECTION
              root; §4: the grid gets this directory as its sole open root)
  val1/     = bars with C1 < date <= C2  (VAL-1 fold, evaluation)
  val2/     = bars with C2 < date <= C3  (VAL-2 fold, evaluation)

C1/C2/C3 are read from the manifest's FROZEN cuts — never recomputed from a
later store (the store grows nightly; recomputation would drift C3, DA c1
P1-1). The holdout span (date > C3) materializes exactly once, later, by
bt_holdout.py — no slice here ever carries a bar past C3, and no slice except
trainval/ ever carries a bar past C2.

Only files that EXIST under streams/ materialize, so streams for excluded
(bar-gate) tickers never appear in a slice by construction. Every slice file
records its own date range (first_bar/last_bar/slice_bars) plus the frozen
cuts it was cut with. Streams/slices share the byte-deterministic gz writer
(bt_streams._dump_lines REUSED — one implementation of that concern).

Read-only w.r.t. the snapshot manifest and streams; writes only under
<snapshot>/slices/. Zero network.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_snapshot                                  # noqa: E402 (manifest seam)
import bt_streams as st                             # noqa: E402 (_dump_lines)

SLICES_DIR = "slices"
FOLDS = ("trainval", "val1", "val2")


class SliceError(RuntimeError):
    """Slicing refused; .reasons names every violated clause (fail-closed)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def fold_keeps(fold: str, d: str, cuts: dict) -> bool:
    """Window membership on the stored naive ISO date (strings compare)."""
    if fold == "trainval":
        return d <= cuts["C2"]
    if fold == "val1":
        return cuts["C1"] < d <= cuts["C2"]
    if fold == "val2":
        return cuts["C2"] < d <= cuts["C3"]
    raise SliceError([f"unknown fold {fold!r} (known: {list(FOLDS)})"])


def read_stream(path: Path) -> tuple[dict, list[dict]]:
    """(header, bars) of one gz stream — the idiom bt_streams wrote."""
    with gzip.open(path, "rb") as f:
        lines = [json.loads(x) for x in f]
    if not lines or "d" in lines[0]:
        raise SliceError([f"{path}: stream doc has no header line"])
    return lines[0], lines[1:]


def materialize_slices(snapshot_dir: Path,
                       folds: tuple[str, ...] = FOLDS) -> dict:
    """Rebuild <snapshot>/slices/<fold>/ from streams + the FROZEN cuts;
    returns {fold: {files, bars, first_bar, last_bar}}. Refuses (SliceError)
    on a missing manifest/streams root, a header without store_snapshot_ts
    (foreign stream), or an out-of-window bar in an existing slice."""
    snapshot_dir = Path(snapshot_dir)
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not mpath.exists():
        raise SliceError([f"no campaign snapshot manifest at {mpath} — run "
                          "bt_snapshot.py build for this store first"])
    cuts = json.loads(mpath.read_text(encoding="utf-8"))["cuts"]
    unknown = [f for f in folds if f not in FOLDS]
    if unknown:
        raise SliceError([f"unknown fold(s) {unknown} (known: {list(FOLDS)})"])
    sroot = snapshot_dir / st.STREAMS_DIR
    if not sroot.is_dir():
        raise SliceError([f"no streams root at {sroot} — run bt_streams.py "
                          "for this snapshot first"])
    summary: dict[str, dict] = {}
    for fold in folds:
        out_root = snapshot_dir / SLICES_DIR / fold
        stats = {"files": 0, "bars": 0, "first_bar": "", "last_bar": ""}
        for src in sorted(sroot.glob("*/*.jsonl.gz")):
            header, bars = read_stream(src)
            if "store_snapshot_ts" not in header:
                raise SliceError([f"{src}: stream header lacks "
                                  "store_snapshot_ts (foreign stream)"])
            kept = [b for b in bars if fold_keeps(fold, b["d"], cuts)]
            doc = dict(header)
            doc.update({"slice": fold, "cuts": {k: cuts[k] for k in
                                                ("C1", "C2", "C3")},
                        "slice_first_bar": kept[0]["d"] if kept else "",
                        "slice_last_bar": kept[-1]["d"] if kept else "",
                        "slice_bars": len(kept),
                        "source_stream": str(src.relative_to(snapshot_dir))})
            rel = src.relative_to(sroot)                # <TICKER>/<IND>.jsonl.gz
            _dump(out_root / rel, [doc] + kept)
            stats["files"] += 1
            stats["bars"] += len(kept)
            if kept:
                stats["first_bar"] = min(stats["first_bar"], kept[0]["d"]) \
                    if stats["first_bar"] else kept[0]["d"]
                stats["last_bar"] = max(stats["last_bar"], kept[-1]["d"])
        summary[fold] = stats
    cg_save(snapshot_dir / SLICES_DIR / "slices_manifest.json", {
        "cuts": cuts, "folds": summary})
    return summary


def _dump(path: Path, docs: list[dict]) -> None:
    """bt_streams' byte-deterministic gz writer — REUSED, not re-implemented."""
    st._dump_lines(path, docs)


def cg_save(path: Path, doc: dict) -> None:
    """Atomic json write via the campaign idiom (bt_chunkgate.save_json)."""
    import bt_chunkgate                              # noqa: WPS433 (late, cheap)
    path.parent.mkdir(parents=True, exist_ok=True)
    bt_chunkgate.save_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-dir", required=True, type=Path,
                    help="campaign snapshot dir (bt_snapshot --out-dir)")
    ap.add_argument("--folds", default=",".join(FOLDS),
                    help=f"comma list from {list(FOLDS)} (default: all)")
    args = ap.parse_args(argv)
    try:
        summary = materialize_slices(
            args.snapshot_dir,
            tuple(f for f in args.folds.split(",") if f.strip()))
    except SliceError as exc:
        print(f"REFUSE — {'; '.join(exc.reasons)}", file=sys.stderr)
        return 1
    for fold, s in summary.items():
        print(f"slices/{fold}: {s['files']} files, {s['bars']} bars, "
              f"{s['first_bar']}..{s['last_bar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
