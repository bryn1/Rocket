"""The combo grid over ONE physical open root (T-BT4, PLAN-c3 §4).
Reason (>250 ln): the verbatim trade-rule port, the physical-holdout open
gate and the per-fold manifest write are ONE concern — PLAN §6/T-BT5 names
bt_combo.py's stream open as THE plant-red site, so it stays in one file;
stdlib only, no new deps.

Fold -> SOLE open root (the c3 P1-2 holdout is PHYSICAL):
    train (SELECTION) -> <snapshot>/slices/train/       bars <= C1 ONLY
    trainval (ceiling)-> <snapshot>/slices/trainval/    bars <= C2 ONLY
    val1 (eval)       -> <snapshot>/slices/val1/        C1 < d <= C2
    val2 (eval)       -> <snapshot>/slices/val2/        C2 < d <= C3

SELECTION (grid screen, pair→shortlist cut, triple grid, C1 re-selection)
runs on slices/train/ — §4 "TRAIN = everything <= C1" + the selection-event
list; §6's "<= C2 SOLE open root" bounds what may be OPENED (a second line
of defence, not the control) and never licensed selecting on VAL-1 (DA-P0-1,
fixed: a (C1,C2] price poison leaves a train pass byte-identical — test).
trainval remains the physical open-ceiling root; val1/val2 are evaluation
folds, never selection inputs.

At EVERY pass start, before any trade is computed:
  1. store_listing_sha256 equality re-asserted against the snapshot manifest
     (bt_snapshot.scan_store + listing_sha256 REUSED — same store, same sha);
  2. the DA-P2-2 OPEN-EVERY-FILE assertion: EVERY file under the open root is
     opened and max(bar date) — over the union of opened dates, read from the
     bars themselves (a filename/`ls` reading is vacuous and BANNED here) —
     must sit inside the fold's frozen window (train <= C1, trainval/val1
     <= C2, val2 <= C3). A full-span stream file copied into a slice trips
     this and the pass REFUSES.
  3. stream-completeness, FAIL CLOSED: every lever in streams_manifest's
     indicators_streamed must be open for every opened ticker (DA-P1-3: a
     mid-chunk resume state missing one (ticker,lever) would SILENTLY shrink
     the grid, the sigma_hat sample and the N price); gaps are named.
     Member date lists must agree per combo (no zip-truncation, DA-P2-4).

Grid = singles + pairs + triples over the SIGNAL-EMITTING levers of THIS
snapshot (streams_manifest n_signals > 0) minus the engine's RISK_ONLY_CLASSES
(IMPORTED) — §5's measured geometry (465 = C(31,2), 4495 = C(31,3)). A combo's
state series is UNANIMOUS confluence (all members BUY -> BUY, all SELL ->
SELL, else HOLD) — the plan leaves combo semantics open; this literal reading
is pinned by tests (RESULT.md names it). The trade rule is ported VERBATIM
from indicator_eval._eval_indicator :131-153 (edge trigger, next-OPEN entry,
opposite-close exit else horizon end, 0.3 % roundtrip IMPORTED from the
engine); the PINNED PARITY TEST compares the emitted round-trip gross lists,
order-for-order, against the engine's own _eval_indicator output. The §4 GATE
series is LONG, PRIMARY H = 10 ONLY (h=5/20 stay descriptive, DA-P1-1).

Per pass the manifest's n_trials_path grows THE RUNNING SUM of ALL selection
events (§4 "accumulates the running sum"): each pass is ONE selection event
(retries re-charge — a run IS an event, §4 never deducts retroactively) and
the fold's key holds that running sum at its latest event, NOT a per-root
bucket (DA-P2-2 fix). Read-modify-write, atomic via bt_chunkgate.save_json.
Zero network; engine files untouched (git diff proves source/rocket/** empty).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO / "source"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_chunkgate as cg                           # noqa: E402 (save_json)
import bt_ds                                        # noqa: E402 (SR row per trial)
import bt_metrics as bm                             # noqa: E402 (§4 metric set)
import bt_slice                                     # noqa: E402 (read_stream)
import bt_snapshot                                  # noqa: E402 (manifest + sha)
from full_universe import CACHE_DIR                 # noqa: E402
from rocket.backtest.indicator_eval import (ROUNDTRIP_PCT,  # noqa: E402
                                            RISK_ONLY_CLASSES)

GRID_DIR = "grid"
STREAM_SUFFIX = ".jsonl.gz"
HORIZONS = (5, 10, 20)
PRIMARY_H = 10
FOLD_ROOT = {"train": "train", "trainval": "trainval",
             "val1": "val1", "val2": "val2"}
FOLD_WINDOW = {"train": ("", "C1"), "trainval": ("", "C2"),
               "val1": ("C1", "C2"), "val2": ("C2", "C3")}


class GridRefused(RuntimeError):
    """Pass refused before any trade; .reasons names every violated clause."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def open_root(snapshot_dir: Path, fold: str) -> dict[str, dict[str, dict]]:
    """Open EVERY file under the fold's sole root; enforce the frozen window
    over the union of OPENED bar dates (DA-P2-2 reading); return
    {ticker: {indicator: {dates, states, opens, closes}}}."""
    if fold not in FOLD_ROOT:
        raise GridRefused([f"unknown fold {fold!r} (known: {list(FOLD_ROOT)})"])
    root = snapshot_dir / bt_slice.SLICES_DIR / FOLD_ROOT[fold]
    files = sorted(root.glob("*/*.jsonl.gz"))
    if not files:
        raise GridRefused([f"open root {root} holds no slice files — run "
                           "bt_slice.py for this snapshot first"])
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not mpath.exists():
        raise GridRefused([f"no campaign snapshot manifest at {mpath}"])
    cuts = json.loads(mpath.read_text(encoding="utf-8"))["cuts"]
    lo_key, hi_key = FOLD_WINDOW[fold]
    lo, hi = (cuts[lo_key] if lo_key else ""), cuts[hi_key]
    out: dict[str, dict[str, dict]] = {}
    worst_bad: list[str] = []
    for path in files:
        _header, bars = bt_slice.read_stream(path)
        dates = [b["d"] for b in bars]
        if not dates:
            continue
        bad = [d for d in dates if d > hi or (lo and d <= lo)]
        if bad:
            worst_bad.append(f"{path.relative_to(root)}: bar date "
                             f"{max(bad)} outside {fold} window "
                             f"({lo or '-inf'} < d <= {hi})")
        tkr, ind = path.parent.name, path.name[: -len(STREAM_SUFFIX)]
        out.setdefault(tkr, {})[ind] = {
            "dates": dates, "states": [b["s"] for b in bars],
            "opens": [b["o"] for b in bars], "closes": [b["c"] for b in bars]}
    if worst_bad:
        raise GridRefused([f"OPEN-EVERY-FILE holdout assertion FAILED for "
                           f"root {root}"] + worst_bad[:8])
    return out


def enumerate_combos(levers: list[str]) -> list[tuple[str, ...]]:
    """singles + pairs + triples (order-stable, levers sorted upstream)."""
    return ([ (l,) for l in levers ]
            + list(itertools.combinations(levers, 2))
            + list(itertools.combinations(levers, 3)))


def combo_states(sets: list[list[str]]) -> list[str]:
    """UNANIMOUS confluence over member state series (pinned reading).
    FAIL CLOSED on unequal member lengths — zip() would silently truncate
    and the grid would trade states that never coexisted on any bar (DA-P2-4)."""
    lens = {len(s) for s in sets}
    if len(lens) != 1:
        raise GridRefused([f"combo members have UNEQUAL state-series lengths "
                           f"{sorted(lens)} — fail closed, never zip-truncate"])
    out = []
    for row in zip(*sets):
        s = row[0]
        out.append(s if all(x == s for x in row) and s != "HOLD" else "HOLD")
    return out


def replay_trades(bars: dict, horizons: tuple[int, ...] = HORIZONS):
    """Trade rule ported VERBATIM from indicator_eval._eval_indicator
    :131-153 (edge trigger; entry at NEXT bar OPEN; exit at the first
    opposite-signal close else horizon end; x beyond data -> incomplete).
    Returns (trades, incomplete_long, incomplete_short, n_buy, n_sell)."""
    states, opens, closes, dates = (bars["states"], bars["opens"],
                                    bars["closes"], bars["dates"])
    n = len(states)
    sig = dict(enumerate(states))
    trades: list[dict] = []
    inc_l = inc_s = n_buy = n_sell = 0
    prev = "HOLD"
    for t in range(n):
        cur = sig[t]
        if cur != "HOLD" and cur != prev:                 # edge trigger (§4)
            side = "long" if cur == "BUY" else "short"
            if side == "long":
                n_buy += 1
            else:
                n_sell += 1
            opposite = "SELL" if side == "long" else "BUY"
            entry = t + 1            # next bar's OPEN is the first tradeable print
            opp_bar = next((j for j in range(entry, n)
                            if sig.get(j) == opposite), None)
            for h in horizons:
                x = opp_bar if opp_bar is not None and opp_bar <= t + h else t + h
                if x >= n:
                    if side == "long":
                        inc_l += 1
                    else:
                        inc_s += 1
                    continue
                entry_px, exit_px = opens[entry], closes[x]
                g = ((exit_px - entry_px) if side == "long"
                     else (entry_px - exit_px)) / entry_px * 100
                trades.append({"signal_bar": dates[t], "entry_date": dates[entry],
                               "exit_date": dates[x], "entry_px": entry_px,
                               "exit_px": exit_px, "side": side, "horizon": h,
                               "entry_i": entry, "exit_i": x,
                               "gross_pct": g, "net_pct": g - ROUNDTRIP_PCT})
        prev = cur
    return trades, inc_l, inc_s, n_buy, n_sell


def _levers(snapshot_dir: Path, bars: dict, want: list[str] | None) -> list[str]:
    """Signal-emitting levers present in the opened bars, risk-only removed
    (§3: dead levers never padded into combos); streams_manifest is truth.
    FAIL CLOSED first (DA-P1-3): every streamed lever must be open for every
    opened ticker — a mid-chunk-state resume missing one (ticker,lever) file
    must REFUSE naming the gap, never silently shrink the sigma_hat sample
    or the N price (§3 requires dead levers PRINTED, not vanished)."""
    man = json.loads((snapshot_dir / "streams_manifest.json")
                     .read_text(encoding="utf-8"))
    gaps = sorted(f"{tkr}/{ind}" for tkr, inds in bars.items()
                  for ind in man.get("indicators_streamed", [])
                  if ind not in inds)
    if gaps:
        raise GridRefused([f"open root INCOMPLETE vs streams_manifest: "
                           f"{len(gaps)} streamed (ticker,lever) stream(s) "
                           "missing — the grid may not silently shrink; "
                           "repair the resume and rerun:"] + gaps[:8]
                          + ([f"(+{len(gaps) - 8} more)"]
                             if len(gaps) > 8 else []))
    counts: dict[str, int] = {}
    for tkr, inds in bars.items():
        for ind, b in inds.items():
            counts[ind] = counts.get(ind, 0) + sum(
                1 for i, s in enumerate(b["states"])
                if s != "HOLD" and s != (b["states"][i - 1] if i else "HOLD"))
    zero = {k for k, v in counts.items() if v == 0}
    dead = set(man.get("zero_signal_indicators", [])) | zero
    common: set[str] = set()
    for inds in bars.values():
        common = set(inds) if not common else common & set(inds)
    levers = sorted(common - RISK_ONLY_CLASSES - dead)
    if want:
        missing = sorted(set(want) - set(levers))
        if missing:
            raise GridRefused([f"requested lever(s) {missing} not gridable on "
                               "this snapshot/root (dead, risk-only, absent)"])
        levers = [l for l in levers if l in set(want)]
    return levers


def run_pass(snapshot_dir: Path, fold: str, store_dir: Path,
             indicators: list[str] | None = None) -> dict:
    """One grid pass over the fold's sole root; writes grid/<fold>/
    trials.json + grows the manifest's n_trials_path[fold]. Refuses naming
    every reason BEFORE opening the grid (sha drift, window trip)."""
    snapshot_dir = Path(snapshot_dir)
    mpath = bt_snapshot.manifest_path(snapshot_dir)
    if not mpath.exists():
        raise GridRefused([f"no campaign snapshot manifest at {mpath}"])
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    listing, _dates = bt_snapshot.scan_store(Path(store_dir))
    if bt_snapshot.listing_sha256(listing) != manifest["store_listing_sha256"]:
        raise GridRefused(["store listing sha != the snapshot's — this pass "
                           "would attribute results to the wrong store"])
    bars = open_root(snapshot_dir, fold)
    levers = _levers(snapshot_dir, bars, indicators)
    if not levers:
        raise GridRefused(["no signal-emitting non-risk-only lever in the "
                           "open root — nothing to grid"])
    combos = enumerate_combos(levers)
    days_set: set[str] = set()
    ticker_days: dict[str, set[str]] = {}
    for tkr, inds in bars.items():
        s = set(next(iter(inds.values()))["dates"])   # levers share the dates
        ticker_days[tkr] = s
        days_set |= s
    days = sorted(days_set)
    fdc = manifest.get("cuts", {}).get("fold_day_counts", {})
    # reconcile ONLY the folds whose stored days ARE the DSR T (VAL folds —
    # every stream starts post-warmup, long before C1, so their windows are
    # fully covered; train/trainval panels legitimately span fewer stream
    # days than store days and are never priced with a T):
    if fold in ("val1", "val2") and fold in fdc and len(days) != fdc[fold]:
        raise GridRefused([f"{fold} open root spans {len(days)} trading days "
                           f"but the frozen manifest says {fdc[fold]} — "
                           "truncated/mangled slices; T would lie"])
    per_day = {d: sum(1 for s in ticker_days.values() if d in s) for d in days}
    trials = []
    for combo in combos:
        trades: list[dict] = []
        in_pos = total_bars = inc_l = inc_s = nbuy = nsell = 0
        for tkr, inds in sorted(bars.items()):
            ref_dates = inds[combo[0]]["dates"]
            drift = [l for l in combo[1:] if inds[l]["dates"] != ref_dates]
            if drift:                     # fail closed (DA-P2-4): misaligned
                raise GridRefused([f"{tkr}: combo {'+'.join(combo)} members "
                                   f"{drift} date lists differ from "
                                   f"{combo[0]}'s — slice mangled; never "
                                   "combine states that never shared a bar"])
            sets = [inds[l]["states"] for l in combo]
            base = dict(inds[combo[0]])
            if len(combo) > 1:
                base["states"] = combo_states(sets)
            tr, i_l, i_s, nb, ns = replay_trades(base)
            trades += [{**t, "ticker": tkr} for t in tr]
            inc_l += i_l
            inc_s += i_s
            nbuy += nb
            nsell += ns
            in_pos += sum(t["exit_i"] - t["entry_i"] + 1 for t in tr
                          if t["side"] == "long" and t["horizon"] == PRIMARY_H)
            total_bars += len(base["dates"])
        daily = bm.panel_daily_series(days, per_day, trades, "long",
                                      PRIMARY_H)   # §4 GATE series = h=10
        st = bt_ds.trial_statistics(daily)
        trials.append({"combo": "+".join(combo), **{k: st[k] for k in
                                                    ("sr", "sigma_nw", "gamma3",
                                                     "gamma4", "n_days")},
                       "evaluable": st["sr"] is not None,
                       "n_trades_long_h10": sum(1 for t in trades if
                                                t["side"] == "long" and
                                                t["horizon"] == PRIMARY_H),
                       "n_buy": nbuy, "n_sell": nsell,
                       "incomplete_dropped": {"long": inc_l, "short": inc_s},
                       "metrics": {"long": bm.metrics_block(
                           trades, "long", PRIMARY_H, in_pos, total_bars,
                           len(bars)),
                           "short": bm.metrics_block(
                           trades, "short", PRIMARY_H, 0, total_bars,
                           len(bars))}})
    n_before = _fold_trials(manifest.get("n_trials_path", 0), fold)
    ntp = _bump(manifest.get("n_trials_path", 0), fold, len(combos))
    doc = {"store_snapshot_ts": {"run_iso": manifest["run_iso"],
                                 "store_listing_sha256":
                                     manifest["store_listing_sha256"]},
           "fold": fold, "cuts": manifest["cuts"],
           "levers": levers, "n_combos_this_pass": len(combos),
           "n_trials_path_before": n_before,
           "n_trials_path_after": ntp,
           "zero_signal_levers_excluded": sorted(
               json.loads((snapshot_dir / "streams_manifest.json")
                          .read_text(encoding="utf-8"))
               .get("zero_signal_indicators", [])),
           "trials": trials}
    out = snapshot_dir / GRID_DIR / fold
    out.mkdir(parents=True, exist_ok=True)
    cg.save_json(out / "trials.json", doc)
    manifest["n_trials_path"] = ntp
    cg.save_json(mpath, manifest)                 # atomic read-modify-write
    return doc


def _fold_trials(ntp, fold: str) -> int:
    return ntp.get(fold, 0) if isinstance(ntp, dict) else 0


def _bump(ntp, fold: str, add: int) -> dict:
    """§4: 'the grid accumulates THE RUNNING SUM' — every pass (screen, cut,
    grid, re-check) is one selection event charging the SAME running sum
    (a retried pass re-charges: each run IS an event, §4 never retroactively
    deducts — the plan-literal, stricter branch; DA-P2-2). The per-fold key
    holds the running sum AT that fold's latest event, plus a 'total' key.
    Never decrements, never rewrites."""
    total = (int(ntp.get("total", max(ntp.values(), default=0))
               if isinstance(ntp, dict)
               else int(ntp)) + add)
    base = dict(ntp) if isinstance(ntp, dict) else {}
    base[fold] = total
    base["total"] = total
    return base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-dir", required=True, type=Path)
    ap.add_argument("--fold", required=True, choices=sorted(FOLD_ROOT))
    ap.add_argument("--store", default=str(CACHE_DIR), type=Path)
    ap.add_argument("--indicators", default="",
                    help="restrict grid to these levers (default: all)")
    args = ap.parse_args(argv)
    cg.courtesy()
    try:
        doc = run_pass(args.snapshot_dir, args.fold, args.store,
                       [i for i in args.indicators.split(",") if i.strip()]
                       or None)
    except (GridRefused, bt_slice.SliceError) as exc:
        print(f"REFUSE — {'; '.join(getattr(exc, 'reasons', [str(exc)]))}",
              file=sys.stderr)
        return 1
    print(f"grid {doc['fold']}: {doc['n_combos_this_pass']} combos over "
          f"{len(doc['levers'])} levers -> trials.json; n_trials_path "
          f"{doc['n_trials_path_before']} -> {doc['n_trials_path_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
