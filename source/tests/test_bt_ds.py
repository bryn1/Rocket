"""bt_ds = PLAN-c3 §4 implemented exactly (T-BT4). Hand-checkable fixtures
only; every trusted check ships the RED proof it can fail: SR*(N=30,σ̂=0.5)
≈ 1.0366 (<1e-3), the denominator² pin 1.5, the fail-closed trio each raising
with its named reason, the planted SR*:=0 mutation flipping a NO-EDGE fixture
FALSE-GREEN (the mutation lives HERE as a monkeypatch, never in bt_ds), and an
iid-noise null-sim whose both-folds pass rate is 0 at a fixed seed. Non-excess
γ4 pinned on the hand series [1,-1,1,-1] (m4/m2² = 1, not −3)."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for _p in (str(TESTS), str(SOURCE / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bt_ds                                        # noqa: E402

N_DAYS = 65                           # ≈ the plan's own VAL-fold geometry


# ── §4(a) SR* closed form: hand fixture + monotone sanity ───────────────────

def test_sr_star_hand_fixture():
    assert abs(bt_ds.sr_star(0.5, 30) - 1.0366) < 1e-3
    assert bt_ds.GAMMA_E == 0.5772156649
    # SR* scales LINEARLY in sigma_hat (the realized spread sets the bar):
    assert bt_ds.sr_star(1.0, 30) == pytest.approx(2.5 * bt_ds.sr_star(0.4, 30))
    assert bt_ds.sr_star(0.5, 300) > bt_ds.sr_star(0.5, 30)   # N inflates it


# ── §4 formula pin: denominator(SR=1, g3=0, g4=3)^2 == 1.5 (non-excess γ4) ──

def test_denominator_nonexcess_gamma4_pin():
    assert bt_ds.denominator(1.0, 0.0, 3.0) ** 2 == pytest.approx(1.5)
    # scipy's EXCESS default (gaussian=0) would have given 0.75 — fork named:
    assert bt_ds.denominator(1.0, 0.0, 0.0) ** 2 == pytest.approx(0.75)
    with pytest.raises(bt_ds.DsrDomainError):       # negative domain: raise
        bt_ds.denominator(3.0, 10.0, 3.0)
    g3, g4 = bt_ds._moments([1.0, -1.0, 1.0, -1.0])  # hand: m2=1 m3=0 m4=1
    assert (g3, g4) == (0.0, 1.0)                    # NON-excess: 1, not -3


# ── §4a fail-closed guards: each names its reason ───────────────────────────

def _trials(n_eval: int, base_sr: float = 0.05, n_dead: int = 0):
    out = [{"combo": f"c{i}", "sr": base_sr + 0.01 * i, "gamma3": 0.0,
            "gamma4": 3.0, "sigma_nw": 1.0} for i in range(n_eval)]
    return out + [{"combo": f"dead{i}", "sr": None, "gamma3": 0.0,
                   "gamma4": 3.0, "sigma_nw": 0.0} for i in range(n_dead)]


def test_guard_N_below_30_raises_named():
    with pytest.raises(bt_ds.NoEdgeGuard) as exc:
        bt_ds.fold_dsr_table(_trials(31), n_trials=29, t_days=N_DAYS)
    assert "29" in str(exc.value) and "n_trials_path" in str(exc.value)


def test_guard_evaluable_below_30_raises_named():
    with pytest.raises(bt_ds.NoEdgeGuard) as exc:
        bt_ds.fold_dsr_table(_trials(29, n_dead=5), n_trials=100,
                             t_days=N_DAYS)
    assert "evaluable" in str(exc.value) and "29" in str(exc.value)


def test_guard_sigma_hat_zero_raises_named():
    identical = _trials(31, base_sr=0.2)
    for t in identical:
        t["sr"] = 0.2                                # trials identical
    with pytest.raises(bt_ds.NoEdgeGuard) as exc:
        bt_ds.fold_dsr_table(identical, n_trials=100, t_days=N_DAYS)
    assert "sigma_hat = 0" in str(exc.value)
    # N=29 AND evaluable<30 BOTH name themselves in one raise:
    with pytest.raises(bt_ds.NoEdgeGuard) as exc:
        bt_ds.fold_dsr_table(_trials(29), n_trials=29, t_days=N_DAYS)
    assert len(exc.value.reasons) == 2


def test_dead_trials_missing_from_sigma_hat_never_zero():
    """A dead trial is ABSENT from the sigma_hat sample, never entered as 0."""
    srs = bt_ds.fold_guard(_trials(31, n_dead=4), n_trials=100)
    assert len(srs) == 31 and all(s is not None for s in srs)
    assert bt_ds.sigma_hat(srs) == pytest.approx(
        bt_ds.sigma_hat([t["sr"] for t in _trials(31)]))


# ── §4(b) fold semantics: BOTH folds independently + sign consistency ───────

def _strong(sr: float, spread: float, n: int = 31):
    return [{"combo": f"c{i}", "sr": sr + spread * i - spread * n / 2,
             "gamma3": 0.0, "gamma4": 3.0, "sigma_nw": 1.0} for i in range(n)]


def _verdict(v1, v2, n1=40, n2=40):
    return bt_ds.evaluate_both_folds(
        v1, v2, {"val1": N_DAYS, "val2": N_DAYS, "train": 70, "holdout": 40},
        {"val1": n1, "val2": n2})


def test_gate_green_needs_both_folds_and_signs():
    strong = _strong(0.62, 0.02)                     # best clears SR* at this T
    v = _verdict(strong, [dict(t) for t in strong])
    assert v["verdict"] == "LEADERBOARD" and len(v["qualifying"]) >= 1
    weak = _strong(0.05, 0.02)                       # single weak fold fails:
    assert all(not c["gate_pass"]                 # BOTH-folds rule
               for c in _verdict(strong, weak)["combos"])
    bad_sign = [{**t, "sr": -abs(t["sr"])} for t in strong]
    assert all(c["sign_consistent"] is False and not c["gate_pass"]
               for c in _verdict(strong, bad_sign)["combos"])


def test_guard_raise_reports_no_edge_with_reason_beside_it():
    v = _verdict(_strong(0.62, 0.02), _strong(0.62, 0.02), n1=29)
    assert v["verdict"] == "NO-EDGE"
    assert v["no_edge_reasons"]["val1"] and "29" in v["no_edge_reasons"]["val1"][0]
    assert "val2" not in v["no_edge_reasons"]       # other fold not punished


# ── the planted RED: SR* := 0 mutation flips NO-EDGE FALSE-GREEN ────────────

def test_planted_sr_star_zero_mutation_flips_noise_false_green(monkeypatch):
    """HONEST gate reads NO-EDGE on this noisy table; the forbidden SR*:=0
    substitution (planted HERE, never in bt_ds) turns the SAME table
    FALSE-GREEN — the anti-c1-P0-1 pin: the gate provably uses SR*."""
    noisy = _strong(0.30, 0.03)                      # sigma_hat ~ 0.27
    assert _verdict(noisy, [dict(t) for t in noisy])["verdict"] == "NO-EDGE"
    best = max(noisy, key=lambda t: t["sr"])
    sh = bt_ds.sigma_hat([t["sr"] for t in noisy])
    d_honest = bt_ds.dsr(best["sr"], 0.0, 3.0, sh, 40, N_DAYS)
    assert d_honest["sr_star"] > 0.5 and d_honest["dsr"] < 0.95
    monkeypatch.setattr(bt_ds, "sr_star", lambda sh_, n_: 0.0)      # PLANT
    assert _verdict(noisy, [dict(t) for t in noisy])["verdict"] == "LEADERBOARD"
    d_planted = bt_ds.dsr(best["sr"], 0.0, 3.0, sh, 40, N_DAYS)
    assert d_planted["sr_star"] == 0.0 and d_planted["dsr"] >= 0.95


# ── null-sim smoke: iid noise, honest gate, BOTH folds, pass rate == 0 ──────

def test_null_sim_iid_noise_both_folds_pass_rate_zero():
    rng = random.Random(20261009)                    # deterministic seed
    reps, n_trials, n_combos = 24, 40, 32
    both_pass = single_pass = 0
    for _ in range(reps):
        folds = []
        for _fold in range(2):
            trials = []
            for c in range(n_combos):
                st = bt_ds.trial_statistics([rng.gauss(0.0, 1.0)
                                             for _d in range(N_DAYS)])
                st["combo"] = f"c{c}"
                trials.append(st)
            folds.append(trials)
        v = _verdict(folds[0], folds[1], n_trials, n_trials)
        both_pass += sum(1 for c in v["combos"] if c["gate_pass"])
        single_pass += sum(1 for c in v["combos"] if c["dsr_val1"] >= 0.95
                           or c["dsr_val2"] >= 0.95)
    assert both_pass == 0                            # §4(b): measured 0.000
    assert single_pass <= 3        # loose single-fold readings DO inflate —
    #                                the honest both-folds gate does not


# ── pooled = DESCRIPTIVE only, never a gate ─────────────────────────────────

def test_pooled_is_labeled_descriptive_and_never_gates():
    pooled = bt_ds.pooled_descriptive(_strong(0.62, 0.02), 40, N_DAYS * 2)
    assert pooled["descriptive_only"] is True
    assert all(r["descriptive_only"] is True for r in pooled["combos"].values())
    bad = bt_ds.pooled_descriptive(_strong(0.62, 0.02), 29, N_DAYS * 2)
    assert bad["descriptive_only"] is True and bad["unavailable"] is True
    v = _verdict(_strong(0.62, 0.02), _strong(0.62, 0.02))
    assert not any("pool" in k.lower() for k in v)   # the gate has no pooled


# ── trial_statistics + Newey-West behaviour pins ────────────────────────────

def test_trial_statistics_dead_series_unevaluable():
    flat = bt_ds.trial_statistics([0.0] * 40)
    assert flat["sr"] is None and flat["sigma_nw"] == 0.0
    assert bt_ds.trial_statistics([0.0] * 30 + [1.0, -1.0] * 5)["sr"] is not None
    with pytest.raises(bt_ds.DsrDomainError):
        bt_ds.trial_statistics([0.0])                # < 2 days: raise, named
    series = [math.sin(i / 8) for i in range(80)]    # positively autocorrelated
    assert bt_ds.newey_west_sd(series) > bt_ds.newey_west_sd(series, lags=0)
