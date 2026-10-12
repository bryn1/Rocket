"""DESCRIPTIVE churn slice per combo over the store partition (T-BT5.1).

PLAN-c3 §3 + verdict semantics (iv): the registry-churn cohort is the only
stability slice this store affords (survivorship is unrepairable post hoc),
so it is reported DESCRIPTIVELY — turnover across the §4 partition classes
(store_io.classify_accounting REUSED, first match wins: scored /
not_fetched / insufficient_rows / dead_at_fetch) plus the SIGN of each
slice only. No threshold, no comparison to one, no result word ever enters
the artifact: §3 forbids reading the slice as a verdict — on these very
files the cohort cannot even sign-agree with itself (RSI +0.21 % vs MACD
-1.17 %/trade).

Churn cohort reading (stated): a store file whose stem is NOT in the
registry plan's regions lists (the §3 plain load_plan().regions cohort —
290 on the c1 store; suffix-normalized 247 is the same fact, looser key);
membership is as-at-run from load_plan() (registry_ts attributes the
read), tests inject members. A snapshot-pinned listing is a COMPLETED
batch, so classify_accounting's not_fetched arm cannot fire here — the
class is still printed, empty.

Machinery is the grid's, imported never re-implemented: bt_combo.open_root
(open-every-file + frozen window), replay_trades + combo_states, PRIMARY_H,
bt_snapshot.scan_store + listing_sha256 (sha re-asserted every run, like
every grid pass). Per (combo, class): ticker counts, trades, in-position
fraction and trades/ticker/year (bm.metrics_block arithmetic, its
BARS_PER_YEAR imported); per-class means are computed only for their SIGN.
Read-only w.r.t. store + snapshot; writes only churn/<fold>/. Zero network.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_chunkgate as cg                           # noqa: E402 (save_json)
import bt_combo as grid                             # noqa: E402 (grid machinery)
import bt_metrics as bm                             # noqa: E402 (BARS_PER_YEAR)
import bt_snapshot                                  # noqa: E402 (store scan)
import bt_streams as st                             # noqa: E402 (STREAMS_DIR)
import store_io                                     # noqa: E402 (§4 partition)
from full_universe import CACHE_DIR, load_plan      # noqa: E402 (registry)

CHURN_DIR = "churn"
CHURN_FILE = "churn_slice.json"
CLASSES = (store_io.SCORED, store_io.NOT_FETCHED,
           store_io.INSUFFICIENT_ROWS, store_io.DEAD_AT_FETCH)
MEMBERSHIP_READING = (
    "membership = plain CSV stem present in the registry plan's regions "
    "lists (PLAN-c3 §3 plain load_plan().regions cohort read); a "
    "snapshot-pinned store listing is a completed batch, so the "
    "not_fetched arm is structurally empty on this re-derivation")


class ChurnRefused(RuntimeError):
    """Slice refused before any write; .reasons names every clause."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def registry_members() -> set[str]:
    """As-at-run registry members (imported load_plan, no network)."""
    plan = load_plan()
    return {t for names in plan.regions.values() for t in names}


def partition_store(
        store_dir: Path, snapshot_dir: Path,
        members: set[str] | None = None,
) -> dict[str, tuple[str, bool]]:
    """{stem: (partition class, in_registry)} over store files ∪ registry
    members, via store_io.classify_accounting (first match wins). The
    campaign's produced_row fact is STREAMED (a ticker contributes to the
    panel iff its stream exists here); has_usable_store is the scan_store
    parse fact ('' listing = unreadable/absent)."""
    listing, _dates = bt_snapshot.scan_store(Path(store_dir))
    if members is None:
        members = registry_members()
    streamed = {p.name for p in Path(snapshot_dir, st.STREAMS_DIR).glob("*/")
                if p.is_dir()}
    out: dict[str, tuple[str, bool]] = {}
    for stem in sorted(set(listing) | {m + ".csv" for m in members}):
        if stem.endswith(".csv"):
            stem = stem[:-4]
        out[stem] = (store_io.classify_accounting(
            produced_row=stem in streamed, batch_completed=True,
            has_usable_store=bool(listing.get(f"{stem}.csv"))),
            stem in members)
    return out


def _sign(vals: list[float]) -> str | None:
    """Sign of the slice — the only reading §4(iv) permits."""
    if not vals:
        return None
    m = statistics.fmean(vals)
    return "positive" if m > 0 else "negative" if m < 0 else "zero"


def _class_row(trs: list[tuple[str, bool]], tickers: set[str],
               in_pos: int, bars: int) -> dict:
    """One (combo, class) cell: turnover + the signs only. trades/tickers/
    bars arithmetic mirrors bm.metrics_block with its BARS_PER_YEAR (no CI
    computed here: the slice stays thin so nobody reads it as a claim)."""
    nets = [net for net, _churn in trs]
    churn_nets = [net for net, churn in trs if churn]
    n_churn_tickers = sum(1 for ch in tickers.values() if ch)
    return {
        "tickers": len(tickers),
        "churn_tickers": n_churn_tickers,
        "n_trades_long_h10": len(trs),
        "churn_n_trades_long_h10": len(churn_nets),
        "in_position_fraction": round(in_pos / bars, 4) if bars else None,
        "trades_per_ticker_year": round(
            len(trs) / len(tickers) * bm.BARS_PER_YEAR / bars, 4)
        if tickers and bars else None,
        "sign": _sign(nets),
        "churn_sign": _sign(churn_nets),
    }


def combo_churn(opened: dict, combo: tuple[str, ...],
                part: dict[str, tuple[str, bool]]) -> dict:
    """Replay one combo on the fold root; group its per-ticker trades by
    partition class. Members missing from a ticker FAIL CLOSED (never a
    silently shrunk panel); member date lists must agree (no truncation)."""
    by_class: dict[str, dict] = {c: {"trs": [], "tickers": {}, "in_pos": 0,
                                     "bars": 0} for c in CLASSES}
    for tkr, inds in sorted(opened.items()):
        missing = [l for l in combo if l not in inds]
        if missing:
            raise ChurnRefused([f"{tkr}: combo {'+'.join(combo)} missing "
                                f"member(s) {missing} in the open root"])
        ref = inds[combo[0]]
        drift = [l for l in combo[1:] if inds[l]["dates"] != ref["dates"]]
        if drift:
            raise ChurnRefused([f"{tkr}: combo {'+'.join(combo)} members "
                                f"{drift} date lists differ from "
                                f"{combo[0]}'s — fail closed"])
        base = dict(ref)
        if len(combo) > 1:
            base["states"] = grid.combo_states(
                [inds[l]["states"] for l in combo])
        tr = grid.replay_trades(base)[0]
        cls, in_reg = part.get(tkr, (store_io.DEAD_AT_FETCH, False))
        cell = by_class[cls]
        cell["trs"] += [(t["net_pct"], not in_reg) for t in tr
                        if t["side"] == "long" and t["horizon"]
                        == grid.PRIMARY_H]
        cell["tickers"][tkr] = not in_reg
        cell["in_pos"] += sum(t["exit_i"] - t["entry_i"] + 1 for t in tr
                              if t["side"] == "long" and t["horizon"]
                              == grid.PRIMARY_H)
        cell["bars"] += len(base["dates"])
    return {c: _class_row(cell["trs"], cell["tickers"], cell["in_pos"],
                          cell["bars"]) for c, cell in by_class.items()}


def run_churn(snapshot_dir: Path, store_dir: Path, fold: str,
              combos: list[str] | None = None, top: int = 0,
              members: set[str] | None = None) -> dict:
    """One descriptive pass; writes churn/<fold>/churn_slice.json (re-runnable
    — no one-shot marker here, it opens nothing that cannot be re-read)."""
    snapshot_dir = Path(snapshot_dir)
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not mpath.exists():
        raise ChurnRefused([f"no campaign snapshot manifest at {mpath}"])
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    listing, _d = bt_snapshot.scan_store(Path(store_dir))
    derived = bt_snapshot.listing_sha256(listing)
    if derived != manifest["store_listing_sha256"]:
        raise ChurnRefused([f"store listing sha {derived} != the snapshot's "
                            f"{manifest['store_listing_sha256']} — this slice "
                            "would attribute results to the wrong store"])
    tpath = snapshot_dir / grid.GRID_DIR / fold / "trials.json"
    if not tpath.exists():
        raise ChurnRefused([f"no grid doc at {tpath} — run bt_combo.py "
                            f"--fold {fold} first"])
    tdoc = json.loads(tpath.read_text(encoding="utf-8"))
    names = [t["combo"] for t in tdoc["trials"]]
    if top:
        ranked = sorted((t for t in tdoc["trials"] if t.get("sr") is not None),
                        key=lambda t: (-t["sr"], t["combo"]))[:top]
        names = [t["combo"] for t in ranked]
    if combos is not None:
        unknown = sorted(set(combos) - set(names))
        if unknown:
            raise ChurnRefused([f"combo(s) {unknown} absent from {tpath}"])
        names = [c for c in names if c in set(combos)]
    opened = grid.open_root(snapshot_dir, fold)   # open-every-file window
    part = partition_store(Path(store_dir), snapshot_dir, members)
    counts = {c: sum(1 for cl, _ in part.values() if cl == c) for c in CLASSES}
    rows = [{"combo": n, "by_class": combo_churn(opened, tuple(n.split("+")),
                                                 part)} for n in names]
    artifact = {
        "descriptive_only": True,
        "store_snapshot_ts": {"run_iso": manifest["run_iso"],
                              "store_listing_sha256":
                                  manifest["store_listing_sha256"]},
        "registry_ts": manifest.get("registry_ts"),
        "membership_reading": MEMBERSHIP_READING,
        "fold": fold, "cuts": manifest["cuts"],
        "universe": {"store_files": len(listing),
                     "registry_members": sum(1 for _, r in part.values() if r),
                     "class_counts": counts},
        "combos": rows,
    }
    out = snapshot_dir / CHURN_DIR / fold
    out.mkdir(parents=True, exist_ok=True)
    cg.save_json(out / CHURN_FILE, artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-dir", required=True, type=Path)
    ap.add_argument("--store", default=str(CACHE_DIR), type=Path)
    ap.add_argument("--fold", default="val2", choices=sorted(grid.FOLD_ROOT))
    ap.add_argument("--combos", default="",
                    help="combo names (default: every trial of the pass)")
    ap.add_argument("--top", default=0, type=int,
                    help="rank the pass by SR-hat and slice only these (0: "
                         "all; T-BT7 will pass the doc's list via --combos)")
    args = ap.parse_args(argv)
    cg.courtesy()
    try:
        doc = run_churn(args.snapshot_dir, args.store, args.fold,
                        [c for c in args.combos.split(",") if c.strip()]
                        or None, args.top)
    except (ChurnRefused, grid.GridRefused) as exc:
        print(f"REFUSE — {'; '.join(getattr(exc, 'reasons', [str(exc)]))}",
              file=sys.stderr)
        return 1
    print(f"churn {args.fold}: {len(doc['combos'])} combos over "
          f"{sum(doc['universe']['class_counts'].values())} partitioned "
          f"tickers -> {CHURN_DIR}/{args.fold}/{CHURN_FILE} "
          "(descriptive_only, snapshot ts carried)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
