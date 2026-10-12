"""One-shot holdout ceremony: the top-K combos' bars > C3 (T-BT5.1, PLAN-c3 §4).

The holdout is PHYSICAL and opens exactly ONCE, after the leaderboard doc is
frozen, so it can never steer the search; §4 verdict semantics (iii) reports
it DESCRIPTIVELY, so no threshold or result word ever enters stdout or the
artifact (labeled descriptive_only; bt_metrics + bt_ds.trial_statistics
IMPORTED, thresholds never computed). Refusals each name their reason
(exit 1). PRE-WRITE clauses: (i) the frozen doc must EXIST and be NON-EMPTY
— a zero-byte file freezes nothing, format is T-BT7's; (ii) the manifest
must carry cuts {C1,C2,C3,fold_day_counts}; (iii) the open list <= 10
(§6 T-BT7), the count named; (iv) re-derived store_listing_sha256 == the
manifest's (bt_snapshot REUSED, both shas named) — enforced on EVERY grid
doc read too; marker: holdout_opened already true -> the span is SPENT (the
marker clause precedes the purge: a spent root is never deleted). Two
POST-MATERIALIZE clauses — the open-every-file > C3 re-check and the day
count — raise AFTER the root is written, but still before the marker and
the artifact: a refused ceremony never spends the span and re-runs clean.

Combos open from --combos (the frozen doc's ratified id-list, order kept;
ids the ranking fold's grid doc never ran REFUSE, naming them) or, by
default, from gate-aware top-K: the ranking fold's SR-hat desc among combos
BOTH VAL folds gate on — evaluable in both, n_trades_long_h10 >= 400 in
both (§4 gate (ii)) and sign-consistent SR-hat (§4 gate (i)'s sign leg;
the DSR leg is bt_ds's/T-BT7's, never re-implemented here). Fewer eligible
than K: take what passes, no padding — zero eligible is a named refusal.

Slice idioms REUSED (bt_slice.read_stream/_dump over <snapshot>/streams/);
slices/holdout/ is PURGED at ceremony start — marker-absent residue is
scratch (F4) — so the artifact's slice_files always equals the physically
present count. Bars > C3 ONLY — none <= C2 ever appears in the root; the
written root is re-read open-every-file (§4 P1-2 reading) and its day count
must reproduce fold_day_counts.holdout before anything is marked. Trades
replay from the holdout span alone (stated: no state crosses C3) for the
member indicators of the opened combos only; n_trials_path is UNCHANGED —
the holdout is no selection event (§4). Zero network.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_chunkgate as cg                           # noqa: E402 (save_json)
import bt_combo as grid                             # noqa: E402 (trade rule)
import bt_ds                                        # noqa: E402 (trial stats)
import bt_metrics as bm                             # noqa: E402 (§4 metric set)
import bt_slice                                     # noqa: E402 (read/dump)
import bt_snapshot                                  # noqa: E402 (manifest+sha)
import bt_streams as st                             # noqa: E402 (STREAMS_DIR)
from full_universe import CACHE_DIR                 # noqa: E402

MAX_TOP_K = 10                           # §6 T-BT7: "top-K <= 10"
GATE_MIN_TRADES = 400                    # §4 gate (ii): n >= 400 per fold
VAL_FOLDS = ("val1", "val2")             # §4 gate (i): the independent pair
HOLDOUT_FOLD = "holdout"                 # slices/holdout/ (bt_slice's FOLDS
HOLDOUT_METRICS = "holdout_metrics.json" #   stay <= C3 — this file is C3<)
MANIFEST_CUT_KEYS = ("C1", "C2", "C3", "fold_day_counts")
STREAM_SUFFIX = ".jsonl.gz"


class HoldoutRefused(RuntimeError):
    """Ceremony refused; .reasons names every clause. The pre-write clauses
    raise before the first write; the post-materialize pair (root re-check,
    day count) raises after the root exists but before marker and artifact."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def _checked_rows(doc: dict, path: Path) -> list[dict]:
    """Trial rows of one grid doc (F6): rows the ceremony ranks on must
    carry combo (name), a numeric-or-null sr and an integer
    n_trades_long_h10 — malformed input names the row and REFUSES, it never
    dies as a bare TypeError mid-sort."""
    trows = doc.get("trials")
    if not isinstance(trows, list):
        raise HoldoutRefused([f"malformed grid doc {path}: no trials list"])
    for i, t in enumerate(trows):
        wrong = (not isinstance(t, dict)
                 or not isinstance(t.get("combo"), str)
                 or not (t.get("sr") is None
                         or isinstance(t.get("sr"), (int, float)))
                 or not isinstance(t.get("n_trades_long_h10"), int))
        if wrong:
            got = t if not isinstance(t, dict) else {
                k: t.get(k) for k in ("combo", "sr", "n_trades_long_h10")}
            raise HoldoutRefused([f"malformed trial row {i} in {path}: needs "
                                  "combo (name), numeric-or-null sr and "
                                  f"integer n_trades_long_h10; got {got}"])
    return trows


def rank_combos(fold_rows: dict[str, list[dict]], fold: str,
                top_k: int) -> list[dict]:
    """Gate-aware default top-K (§4): the RANKING fold's rows, SR-hat desc
    (combo name = stable tie-break), restricted to combos BOTH VAL folds
    gate on — evaluable in both, n_trades_long_h10 >= GATE_MIN_TRADES in
    both (gate (ii)) and sign-consistent SR-hat across the folds (gate (i)'s
    sign leg; the DSR leg is bt_ds's, never re-implemented here). Fewer
    eligible than K: take what passes, no padding."""
    per = {f: {t["combo"]: t for t in fold_rows[f]} for f in VAL_FOLDS}
    eligible = set()
    for name in per[VAL_FOLDS[0]]:
        if name not in per[VAL_FOLDS[1]]:
            continue                       # gate (i) needs BOTH folds
        pair = [per[f][name] for f in VAL_FOLDS]
        if (all(t["sr"] is not None for t in pair)
                and all(t["n_trades_long_h10"] >= GATE_MIN_TRADES
                        for t in pair)
                and pair[0]["sr"] * pair[1]["sr"] > 0):
            eligible.add(name)
    ev = [t for t in fold_rows[fold] if t["combo"] in eligible
          and t["sr"] is not None]
    return sorted(ev, key=lambda t: (-t["sr"], t["combo"]))[:top_k]


def select_combos(trows: list[dict], names: list[str]) -> list[dict]:
    """--combos override: the frozen doc's ratified list, in the given
    order, from the ranking fold's rows (ids unique-preserved); ids the
    grid pass never ran REFUSE, naming the unknowns."""
    by = {t["combo"]: t for t in trows}
    want = list(dict.fromkeys(names))
    missing = [n for n in want if n not in by]
    if missing:
        raise HoldoutRefused([f"--combos id(s) absent from this fold's grid "
                              f"doc: {missing} — the ceremony opens only "
                              "combos the grid pass actually ran"])
    return [by[n] for n in want]


def _fold_rows(snapshot_dir: Path, fold: str, snap_sha: str
               ) -> tuple[Path, list[dict]]:
    """One fold's grid doc: must EXIST, must carry the snapshot's store sha
    (a stale pass doc misattributes), rows F6-checked. Returns (path, rows)."""
    p = snapshot_dir / grid.GRID_DIR / fold / "trials.json"
    if not p.exists():
        raise HoldoutRefused([f"no grid doc at {p} — run bt_combo.py --fold "
                              f"{fold} first (ranking source)"])
    doc = json.loads(p.read_text(encoding="utf-8"))
    if (doc.get("store_snapshot_ts", {})
            .get("store_listing_sha256") != snap_sha):
        raise HoldoutRefused([f"{p} store_snapshot_ts sha != the snapshot's "
                              "— stale grid doc, misattributed"])
    return p, _checked_rows(doc, p)


def _open_holdout_root(out_root: Path, c3: str) -> dict[str, dict[str, dict]]:
    """OPEN-EVERY-FILE self-check of the WRITTEN root: every file re-read,
    every bar must be > C3 — so none <= C2 either (the §4 P1-2 reading).
    Returns {ticker: {indicator: bars}} shaped like grid.open_root."""
    files = sorted(out_root.glob(f"*/*{STREAM_SUFFIX}"))
    if not files:
        raise HoldoutRefused([f"holdout root {out_root} holds no files — "
                              "nothing materialized (streams empty?)"])
    opened: dict[str, dict[str, dict]] = {}
    bad: list[str] = []
    for path in files:
        _header, bars = bt_slice.read_stream(path)
        late = [b["d"] for b in bars if b["d"] <= c3]
        if late:
            bad.append(f"{path}: {len(late)} bar(s) at-or-before C3 {c3} "
                       f"(earliest offender {min(late)})")
            continue
        opened.setdefault(path.parent.name, {})[
            path.name[: -len(STREAM_SUFFIX)]] = {
                "dates": [b["d"] for b in bars], "states": [b["s"] for b in bars],
                "opens": [b["o"] for b in bars], "closes": [b["c"] for b in bars]}
    if bad:
        raise HoldoutRefused([f"HOLDOUT span assertion FAILED over "
                              f"{out_root} (open-every-file)"] + bad[:8])
    return opened


def descriptive_rows(opened: dict, ranked: list[dict]) -> list[dict]:
    """Per-combo DESCRIPTIVE rows on the holdout span — the grid's own
    machinery; thresholds are never computed here."""
    ticker_days = {tkr: set(next(iter(inds.values()))["dates"])
                   for tkr, inds in opened.items()}
    days = sorted(set().union(*ticker_days.values())) if ticker_days else []
    per_day = {d: sum(1 for s in ticker_days.values() if d in s) for d in days}
    rows = []
    for trial in ranked:
        combo = tuple(trial["combo"].split("+"))
        trades: list[dict] = []
        in_pos = total_bars = 0
        for tkr, inds in sorted(opened.items()):
            missing = [l for l in combo if l not in inds]
            if missing:                     # fail closed, never shrink panel
                raise HoldoutRefused([f"{tkr}: combo {'+'.join(combo)} "
                                      f"missing members {missing} — "
                                      "fail closed"])
            base = dict(inds[combo[0]])
            if len(combo) > 1:
                base["states"] = grid.combo_states(
                    [inds[l]["states"] for l in combo])
            tr = grid.replay_trades(base)[0]
            trades += [{**t, "ticker": tkr} for t in tr]
            in_pos += sum(t["exit_i"] - t["entry_i"] + 1 for t in tr
                          if t["side"] == "long" and t["horizon"]
                          == grid.PRIMARY_H)
            total_bars += len(base["dates"])
        daily = bm.panel_daily_series(days, per_day, trades, "long",
                                      grid.PRIMARY_H)
        stt = bt_ds.trial_statistics(daily)
        rows.append({"combo": "+".join(combo),
                     "daily_series": {k: round(stt[k], 6) for k in
                                      ("mean", "sigma_nw", "sr")
                                      if stt[k] is not None} |
                                     {k: stt[k] for k in ("gamma3", "gamma4",
                                                          "n_days")},
                     "long_h10": bm.metrics_block(trades, "long",
                                                  grid.PRIMARY_H, in_pos,
                                                  total_bars, len(opened))})
    return rows


def open_holdout(snapshot_dir: Path, store_dir: Path, doc_path: Path,
                 top_k: int = MAX_TOP_K, fold: str = "val2",
                 combos: list[str] | None = None) -> dict:
    """Run the ceremony; returns the written artifact. The pre-write clauses
    refuse before the first write; the open-every-file re-check and the day
    count refuse AFTER materializing but still before marker and artifact —
    a failed ceremony is re-runnable, a completed one never is."""
    snapshot_dir = Path(snapshot_dir)
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not 1 <= top_k <= MAX_TOP_K:
        raise HoldoutRefused([f"top-K = {top_k} outside 1..{MAX_TOP_K}: the "
                              "ceremony opens at most 10 combos (§6 T-BT7)"])
    if not mpath.exists():
        raise HoldoutRefused([f"no campaign snapshot manifest at {mpath} — "
                              "run bt_snapshot.py build for this store first"])
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    cuts = manifest.get("cuts", {})
    missing = [k for k in MANIFEST_CUT_KEYS if k not in cuts]
    if missing:
        raise HoldoutRefused([f"snapshot manifest invalid: cuts lack "
                              f"{missing} ({mpath})"])
    if manifest.get("holdout_opened"):
        raise HoldoutRefused([f"holdout already opened: holdout_opened is "
                              f"true in {mpath} — the span is spent"])
    if not doc_path.exists():
        raise HoldoutRefused([f"leaderboard doc absent: {doc_path} — the doc "
                              "must be FROZEN first (PLAN-c3 §4)"])
    if doc_path.exists() and doc_path.stat().st_size == 0:
        raise HoldoutRefused([f"leaderboard doc is ZERO bytes: {doc_path} — "
                              "an empty file freezes nothing; a touch may "
                              "not spend the one-shot span (PLAN-c3 §4)"])
    listing, _dates = bt_snapshot.scan_store(Path(store_dir))
    derived_sha = bt_snapshot.listing_sha256(listing)
    snap_sha = manifest["store_listing_sha256"]
    if derived_sha != snap_sha:
        raise HoldoutRefused([f"store listing sha {derived_sha} != the "
                              f"snapshot's {snap_sha} — the holdout would "
                              "be attributed to a drifted store"])
    tpath, trows = _fold_rows(snapshot_dir, fold, snap_sha)
    if combos:
        names = list(dict.fromkeys(combos))
        if not 1 <= len(names) <= MAX_TOP_K:
            raise HoldoutRefused([f"--combos list of {len(names)} combo(s) "
                                  f"outside 1..{MAX_TOP_K}: the ceremony "
                                  "opens at most 10 combos (§6 T-BT7)"])
        ranked = select_combos(trows, names)
        mode = "explicit"
    else:
        fold_rows = {fold: trows}
        for f in VAL_FOLDS:
            if f not in fold_rows:
                fold_rows[f] = _fold_rows(snapshot_dir, f, snap_sha)[1]
        ranked = rank_combos(fold_rows, fold, top_k)
        mode = "auto"
    if not ranked:
        raise HoldoutRefused([
            f"no gate-eligible combo across grid/{VAL_FOLDS[0]} + "
            f"grid/{VAL_FOLDS[1]}: nothing is evaluable in both folds with "
            f"n_trades_long_h10 >= {GATE_MIN_TRADES} in each and "
            "sign-consistent SR-hat — what passes opens, never padding to K "
            "(§4 leaderboard is EMPTY at this scale)"])
    c3 = cuts["C3"]
    want = sorted({i for t in ranked for i in t["combo"].split("+")})
    sroot = snapshot_dir / st.STREAMS_DIR
    out_root = snapshot_dir / bt_slice.SLICES_DIR / HOLDOUT_FOLD
    if out_root.exists():
        shutil.rmtree(out_root)      # marker-absent residue is scratch (F4)
    n_files = 0
    for src in sorted(sroot.glob(f"*/*{STREAM_SUFFIX}")):
        if src.name[: -len(STREAM_SUFFIX)] not in want:
            continue                        # K bounds what may be opened
        header, bars = bt_slice.read_stream(src)
        kept = [b for b in bars if b["d"] > c3]   # > C3 ONLY (never <= C2)
        doc = dict(header)
        doc.update({"slice": HOLDOUT_FOLD,
                    "cuts": {k: cuts[k] for k in ("C1", "C2", "C3")},
                    "slice_first_bar": kept[0]["d"] if kept else "",
                    "slice_last_bar": kept[-1]["d"] if kept else "",
                    "slice_bars": len(kept),
                    "source_stream": str(src.relative_to(snapshot_dir))})
        bt_slice._dump(out_root / src.relative_to(sroot), [doc] + kept)
        n_files += 1
    opened = _open_holdout_root(out_root, c3)   # open-every-file, re-read
    hold_days = len({d for inds in opened.values() for bars in inds.values()
                     for d in bars["dates"]})
    fdc = cuts["fold_day_counts"]
    if "holdout" in fdc and hold_days != fdc["holdout"]:
        raise HoldoutRefused([f"holdout root spans {hold_days} trading "
                              f"days, manifest says {fdc['holdout']} — the "
                              "descriptive T would lie"])
    desc = descriptive_rows(opened, ranked)
    artifact = {"descriptive_only": True,
                "store_snapshot_ts": {"run_iso": manifest["run_iso"],
                                      "store_listing_sha256": snap_sha},
                "cuts": cuts,
                "ranking_source": {"fold": fold, "mode": mode,
                                   "path": str(tpath.relative_to(
                                       snapshot_dir))} |
                                  ({"ids": [t["combo"] for t in ranked]}
                                   if mode == "explicit" else {}),
                "top_k": len(ranked), "slice_files": n_files, "combos": desc}
    cg.save_json(snapshot_dir / HOLDOUT_METRICS, artifact)
    manifest["holdout_opened"] = True            # marker LAST: spent span
    cg.save_json(mpath, manifest)
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-dir", required=True, type=Path)
    ap.add_argument("--store", default=str(CACHE_DIR), type=Path)
    ap.add_argument("--doc", required=True, type=Path,
                    help="frozen leaderboard doc (existence + non-empty "
                    "content = the freeze precondition)")
    ap.add_argument("--top", default=MAX_TOP_K, type=int,
                    help=f"top-K combos to open (1..{MAX_TOP_K})")
    ap.add_argument("--fold", default="val2", choices=sorted(grid.FOLD_ROOT),
                    help="grid pass to rank combos from (default val2)")
    ap.add_argument("--combos", default="",
                    help="comma list of combo ids to open — the frozen "
                    "doc's ratified set, order kept (default: gate-aware "
                    "top-K; the ids must exist in --fold's trials.json)")
    args = ap.parse_args(argv)
    cg.courtesy()
    try:
        doc = open_holdout(args.snapshot_dir, args.store, args.doc,
                           args.top, args.fold,
                           [c.strip() for c in args.combos.split(",")
                            if c.strip()] or None)
    except (HoldoutRefused, grid.GridRefused, bt_slice.SliceError) as exc:
        print(f"REFUSE — {'; '.join(getattr(exc, 'reasons', [str(exc)]))}",
              file=sys.stderr)
        return 1
    print(f"holdout opened once: {doc['slice_files']} files (bars > "
          f"{doc['cuts']['C3']}), {len(doc['combos'])} combos -> "
          f"{HOLDOUT_METRICS} (descriptive_only, snapshot ts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
