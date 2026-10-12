"""bt_holdout FIX-CYCLE-1 (MC 10477, DA-verdict 202610120126): the guards
that shipped red-less, proven red, plus the new clauses' pairs.
F1 — the post-materialize open-every-file re-check (a <= C2 file PLANTED
mid-write into the root -> refusal; flipped -> the plant sails through) and
the day-count clause (wrong-but-present fold_day_counts.holdout -> refusal
naming both counts; inverted -> a T that lies ships).
F2 — --doc must be NON-EMPTY (zero-byte -> refusal naming it; flipped -> an
empty doc pays the one-shot span).
F3 — gate-aware default ranking (fixture scale cannot clear n>=400 -> named
refusal; zeroed gate -> exactly the both-fold-evaluable, sign-consistent
combos open, take-what-passes no padding) and the --combos override (doc
order kept, unknown ids refused naming them, list capped at 10 — widened
cap -> 11 open).
F6 — malformed trials.json -> NAMED refusal (flipped -> bare TypeError).
F4 — the ceremony PURGES slices/holdout/ at start (stale residue gone,
slice_files == physical; purge off -> residue survives and the artifact
miscounts)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_combo as gc
import bt_holdout as bh
from bt_fold_fixtures import make_store, ready_snapshot
from bt_holdout_helpers import (SIX, frozen_doc, grid_ids, holdout_root,
                                manifest_of, mutated_holdout,
                                physical_files, plant_gt_c3,
                                plant_stale_le_c3, ready_grid, run_cli,
                                write_manifest)


# ── F1: the two POST-MATERIALIZE guards, proven red ─────────────────────────

def test_planted_le_c3_file_refuses_open_every_file(tmp_path, monkeypatch):
    """The §4 P1-2 guard, RED as shipped: a <= C2 file PLANTED into the
    write root mid-ceremony -> the open-every-file re-check refuses, naming
    the planted file and its earliest offender. The manifest day count is
    tampered to the count a SURVIVING plant would produce, so only the
    guard clause can be what stops this ceremony."""
    store, snap = ready_grid(tmp_path)
    extra = plant_stale_le_c3(monkeypatch, snap)
    man = manifest_of(snap)
    true_days = man["cuts"]["fold_day_counts"]["holdout"]
    man["cuts"]["fold_day_counts"]["holdout"] = true_days + extra
    write_manifest(snap, man)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                        ["MACD"])
    r = " ; ".join(exc.value.reasons)
    assert "span assertion" in r and "STALE.jsonl.gz" in r
    assert "at-or-before C3" in r and "trading days" not in r   # guard first
    assert manifest_of(snap)["holdout_opened"] is False  # span never spent
    assert not (snap / bh.HOLDOUT_METRICS).exists()      # no artifact


def test_open_every_file_flip_accepts_the_plant(tmp_path, monkeypatch):
    """RED pair: disable ONLY the > C3 re-check comparison -> the SAME
    planted file sails through the re-read and the ceremony COMPLETES (with
    the tampered day count matching) — the refusal above was that clause."""
    store, snap = ready_grid(tmp_path)
    extra = plant_stale_le_c3(monkeypatch, snap)
    man = manifest_of(snap)
    man["cuts"]["fold_day_counts"]["holdout"] += extra
    write_manifest(snap, man)
    mod = mutated_holdout(
        ('        late = [b["d"] for b in bars if b["d"] <= c3]',
         '        late = [b["d"] for b in bars if b["d"] < ""]'))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                           ["MACD"])
    assert art["descriptive_only"]                       # plant never caught
    assert manifest_of(snap)["holdout_opened"] is True


def test_day_count_tamper_refuses_naming_both_counts(tmp_path):
    """The other post-materialize guard: manifest fdc.holdout wrong-BUT-
    present (true+5) -> refusal names the measured days AND the manifest's
    lie; the marker and artifact never land."""
    store, snap = ready_grid(tmp_path)
    man = manifest_of(snap)
    true_days = man["cuts"]["fold_day_counts"]["holdout"]
    man["cuts"]["fold_day_counts"]["holdout"] = true_days + 5
    write_manifest(snap, man)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                        ["MACD"])
    r = " ; ".join(exc.value.reasons)
    assert f"spans {true_days} trading days" in r
    assert f"manifest says {true_days + 5}" in r
    assert manifest_of(snap)["holdout_opened"] is False
    assert not (snap / bh.HOLDOUT_METRICS).exists()


def test_day_count_flip_proceeds_on_the_tampered_count(tmp_path):
    """RED pair: invert ONLY the day-count comparison -> the same tampered
    manifest is accepted and the artifact ships with a T that lies — the
    refusal above was that clause."""
    store, snap = ready_grid(tmp_path)
    man = manifest_of(snap)
    man["cuts"]["fold_day_counts"]["holdout"] += 5
    write_manifest(snap, man)
    mod = mutated_holdout(
        ('if "holdout" in fdc and hold_days != fdc["holdout"]:',
         'if "holdout" in fdc and hold_days == fdc["holdout"]:'))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                           ["MACD"])
    assert art["descriptive_only"]
    assert manifest_of(snap)["holdout_opened"] is True


# ── F2: --doc must be NON-EMPTY ─────────────────────────────────────────────

def test_zero_byte_doc_refuses_naming_empty(tmp_path):
    store, snap = ready_grid(tmp_path)
    doc = tmp_path / "LEADERBOARD.md"
    doc.write_bytes(b"")                                 # a bare touch
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    r = " ; ".join(exc.value.reasons)
    assert "ZERO bytes" in r and str(doc) in r
    assert manifest_of(snap)["holdout_opened"] is False  # span not spent
    assert not holdout_root(snap).exists()


def test_zero_byte_doc_flip_opens_on_an_empty_doc(tmp_path):
    """RED pair: disable ONLY the non-empty check -> a zero-byte doc pays
    the one-shot span (the DA's P1.rerun repro) — the refusal above was
    that clause."""
    store, snap = ready_grid(tmp_path)
    doc = tmp_path / "LEADERBOARD.md"
    doc.write_bytes(b"")
    mod = mutated_holdout(
        ("if doc_path.exists() and doc_path.stat().st_size == 0:",
         "if False:"))
    art = mod.open_holdout(snap, store, doc, 10, "val2", ["MACD"])
    assert art["descriptive_only"]
    assert manifest_of(snap)["holdout_opened"] is True   # the span is GONE


# ── F3: gate-aware ranking + --combos override ──────────────────────────────

def test_gate_default_refuses_when_nothing_passes(tmp_path):
    """Fixture scale CANNOT clear §4 gate (ii) (n>=400/fold; measured 6-7
    trades here) -> the gate-aware DEFAULT refuses naming the clause;
    nothing opens, nothing spends."""
    store, snap = ready_grid(tmp_path)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")
    r = " ; ".join(exc.value.reasons)
    assert "gate-eligible" in r and str(bh.GATE_MIN_TRADES) in r
    assert "sign-consistent" in r
    assert not holdout_root(snap).exists()
    assert not (snap / bh.HOLDOUT_METRICS).exists()
    assert manifest_of(snap)["holdout_opened"] is False


def test_gate_flip_opens_both_fold_consistent(tmp_path):
    """RED pair for the n>=400 leg at ceremony scale: zero the threshold ->
    the ceremony completes with EXACTLY the both-fold-evaluable, sign-
    consistent combos (MACD, RSI by val2 SR-hat desc); MACD+RSI stays out —
    the evaluable leg still gates it. top_k records the 2 actually opened:
    take-what-passes, no padding to K=10."""
    store, snap = ready_grid(tmp_path)
    mod = mutated_holdout(("GATE_MIN_TRADES = 400", "GATE_MIN_TRADES = 0"))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")
    assert [c["combo"] for c in art["combos"]] == ["MACD", "RSI"]
    assert art["top_k"] == 2 and art["ranking_source"]["mode"] == "auto"


def _trow(combo, sr, n=500):
    return {"combo": combo, "sr": sr, "n_trades_long_h10": n}


SYNTH = {                                        # one row per VAL fold, same
    "val1": [_trow("GOOD", 0.5), _trow("GOOD2", 0.55, 400),
             _trow("LOW-N", 0.4), _trow("SWING", 0.8),
             _trow("ONLY-V1", 0.9, 900), _trow("UNEVAL", None)],
    "val2": [_trow("GOOD", 0.6), _trow("GOOD2", 0.9, 401),
             _trow("LOW-N", 0.3, 399), _trow("SWING", -0.1, 900),
             _trow("UNEVAL", None, 900)],
}


def test_rank_combos_gates_every_leg():
    """Unit GREEN: only GOOD + GOOD2 are evaluable in BOTH folds with
    n>=400 each and sign-consistent SR-hat; rank = val2 SR-hat desc, rows
    are the RANKING fold's (GOOD2 carries n 401); K below the eligible
    count truncates, never pads."""
    res = bh.rank_combos(SYNTH, "val2", 10)
    assert [t["combo"] for t in res] == ["GOOD2", "GOOD"]
    assert res[0]["n_trades_long_h10"] == 401            # val2's rows
    assert [t["combo"] for t in bh.rank_combos(SYNTH, "val2", 1)] == ["GOOD2"]


def test_rank_combos_n_leg_flip_admits_low_trade_combo():
    """RED pair: drop ONLY the n>=400 leg -> LOW-N (399 trades on val2)
    ranks in; the unit refusal above was that leg."""
    mod = mutated_holdout(
        ('and all(t["n_trades_long_h10"] >= GATE_MIN_TRADES\n'
         '                        for t in pair)', "and True"))
    res = mod.rank_combos(SYNTH, "val2", 10)
    assert "LOW-N" in [t["combo"] for t in res]


def test_rank_combos_sign_leg_flip_admits_sign_flip():
    """RED pair: drop ONLY the sign-consistency leg -> SWING (+0.8 on val1,
    -0.1 on val2) ranks in; the unit refusal above was that leg."""
    mod = mutated_holdout(
        ('and pair[0]["sr"] * pair[1]["sr"] > 0):', "and True):"))
    res = mod.rank_combos(SYNTH, "val2", 10)
    assert "SWING" in [t["combo"] for t in res]


def test_combos_override_opens_named_list_in_order(tmp_path):
    store, snap = ready_grid(tmp_path)
    art = bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                          ["RSI", "MACD"])
    assert [c["combo"] for c in art["combos"]] == ["RSI", "MACD"]  # doc order
    assert art["ranking_source"]["ids"] == ["RSI", "MACD"]
    assert art["ranking_source"]["mode"] == "explicit"
    assert art["slice_files"] == len(physical_files(snap)) == 6


def test_combos_override_refuses_unknown_ids_naming_them(tmp_path):
    store, snap = ready_grid(tmp_path)
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                        ["MACD", "NOPE+X", "GONE"])
    r = " ; ".join(exc.value.reasons)
    assert "NOPE+X" in r and "GONE" in r
    assert "['MACD', 'NOPE+X', 'GONE']" not in r        # known id not named
    assert not holdout_root(snap).exists()


def test_combos_unknown_flip_crashes_instead(tmp_path):
    """RED pair: disable ONLY the unknown-id check -> the same list dies
    unnamed (KeyError) deeper — the refusal above is that clause."""
    store, snap = ready_grid(tmp_path)
    mod = mutated_holdout(
        ('    if missing:\n        raise HoldoutRefused([f"--combos id(s)',
         '    if missing and False:\n        raise HoldoutRefused([f"--combos'
         ' id(s)'))
    with pytest.raises(KeyError):
        mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                         ["MACD", "NOPE"])


def test_combos_list_cap_refuses_naming_count(tmp_path):
    store, snap = ready_grid(tmp_path)
    ids = [f"X{i}" for i in range(11)]
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2", ids)
    r = " ; ".join(exc.value.reasons)
    assert "11" in r and "10" in r
    assert not holdout_root(snap).exists()


def test_combos_list_cap_flip_opens_eleven(tmp_path):
    """RED pair: widen ONLY the explicit-list cap -> an 11-combo ratified
    list opens (artifact top_k 11) over a wider (6-lever, 41-combo) grid
    pass; the refusal above was the cap clause alone."""
    store = make_store(tmp_path, ["AA", "BB", "CC"])
    snap = ready_snapshot(tmp_path, store, ["AA", "BB", "CC"], SIX)
    gc.run_pass(snap, "val2", store, SIX)
    ids = grid_ids(snap)[:11]
    assert len(ids) == 11
    mod = mutated_holdout(("if not 1 <= len(names) <= MAX_TOP_K:",
                           "if not 1 <= len(names) <= 10 ** 6:"))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2", ids)
    assert art["top_k"] == 11 and len(art["combos"]) == 11


# ── F6: malformed trials.json -> NAMED refusal ──────────────────────────────

def test_malformed_trials_json_refuses_named(tmp_path):
    store, snap = ready_grid(tmp_path)
    p = snap / "grid" / "val2" / "trials.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["trials"][0]["sr"] = "9.9"                       # non-numeric SR-hat
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(bh.HoldoutRefused) as exc:
        bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")
    r = " ; ".join(exc.value.reasons)
    assert "malformed trial row" in r and "val2" in r
    assert manifest_of(snap)["holdout_opened"] is False


def test_malformed_check_flip_crashes_unnamed(tmp_path):
    """RED pair: disable ONLY the row check (and zero the gate so the same
    rows rank at all) -> the tampered doc dies as a bare TypeError, the
    failure mode F6 named — the check turns it into a refusal."""
    store, snap = ready_grid(tmp_path)
    p = snap / "grid" / "val2" / "trials.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["trials"][0]["sr"] = "9.9"
    p.write_text(json.dumps(doc), encoding="utf-8")
    mod = mutated_holdout(("        if wrong:", "        if wrong and False:"),
                          ("GATE_MIN_TRADES = 400", "GATE_MIN_TRADES = 0"))
    with pytest.raises(TypeError):
        mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2")


# ── F4: purge at start -> artifact count == physical count ─────────────────

def test_ceremony_purges_stale_residue(tmp_path):
    """Stale residue — a <= C2 file AND a legal post-C3 leftover — planted
    BEFORE the ceremony must not survive: marker-absent root is scratch, so
    the artifact's slice_files always equals the physically present count."""
    store, snap = ready_grid(tmp_path)
    c3 = manifest_of(snap)["cuts"]["C3"]
    root = holdout_root(snap)
    (root / "AA").mkdir(parents=True)
    (root / "AA" / "RSI.jsonl.gz").write_bytes(
        (snap / "slices" / "trainval" / "AA" / "RSI.jsonl.gz").read_bytes())
    plant_gt_c3(snap, "ZZ", "MACD", c3)
    art = bh.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                          ["MACD"])
    assert not (root / "AA" / "RSI.jsonl.gz").exists()   # stale <= C2 gone
    assert not (root / "ZZ").exists()                    # stale residue gone
    assert art["slice_files"] == len(physical_files(snap)) == 3


def test_purge_flip_survives_and_miscounts(tmp_path):
    """RED pair: disable ONLY the purge -> the legal-looking residue
    survives into the re-opened root, and the artifact's slice_files (3)
    no longer equals what is physically present (4) — the F4 miscount; the
    cleanup above was that clause."""
    store, snap = ready_grid(tmp_path)
    c3 = manifest_of(snap)["cuts"]["C3"]
    plant_gt_c3(snap, "ZZ", "MACD", c3)
    mod = mutated_holdout(("    if out_root.exists():", "    if False:"))
    art = mod.open_holdout(snap, store, frozen_doc(tmp_path), 10, "val2",
                           ["MACD"])
    assert (holdout_root(snap) / "ZZ" / "MACD.jsonl.gz").exists()
    assert art["slice_files"] == 3
    assert len(physical_files(snap)) == 4                # artifact miscounts


# ── CLI: the new clauses ride the exit-code contract too ────────────────────

def test_cli_zero_byte_doc_and_unknown_combo_refuse(tmp_path):
    store, snap = ready_grid(tmp_path)
    empty = tmp_path / "EMPTY.md"
    empty.write_bytes(b"")
    bad = run_cli(snap, store, empty, "--combos", "MACD")
    assert bad.returncode == 1 and "ZERO bytes" in bad.stderr
    assert manifest_of(snap)["holdout_opened"] is False  # still unspent
    bad2 = run_cli(snap, store, frozen_doc(tmp_path), "--combos", "NOPE")
    assert bad2.returncode == 1 and "NOPE" in bad2.stderr
    ok = run_cli(snap, store, frozen_doc(tmp_path), "--combos", "MACD")
    assert ok.returncode == 0 and "holdout opened once" in ok.stdout
