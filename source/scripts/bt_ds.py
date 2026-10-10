"""Deflated Sharpe Ratio exactly as PLAN-c3 §4 pins it (T-BT4, honesty core).
Reason: single concern — the §4 gate; every pinned quantity (SR*, NW L=10,
non-excess γ4, guard trio, both-folds rule, pooled-as-descriptive) lives in
ONE file so the convention cannot fork; stdlib NormalDist, no new deps.

  * SR  = mean(r)/sigma_NW(r) on the combo's panel daily series (trade net %
    attributed to the EXIT bar's date, 0 where flat); sigma_NW = Newey-West
    HAC long-run SD, bandwidth L = 10 = the primary horizon, Bartlett weights
    1 - l/(L+1) — one hand-written stdlib loop.
  * SR* = sigma_hat * [(1 - GAMMA_E)*Phi^-1(1 - 1/N) + GAMMA_E*Phi^-1(1 -
    1/(N*e))], GAMMA_E = 0.5772156649; sigma_hat = sample SD (ddof = 1)
    ACROSS the realized trial SR-hats of the fold's candidate set — NOT the
    null expectation (E[max]=0 passes noise at 0.983, DA c2 — forbidden code;
    `SR* := 0` likewise). N = n_trials_path of that fold's selection events
    (a SEPARATE named quantity from the evaluable-count guard).
  * DSR = Phi((SR - SR*)*sqrt(T - 1) / sqrt(1 - g3*SR + (g4 - 1)/4*SR^2));
    g4 NON-EXCESS (m4/m2^2 — gaussian ⇒ 3 ⇒ +1/2 in the term; scipy defaults
    to EXCESS and would silently halve it; stdlib-only here so the
    convention lives in this line + its test: denominator(1,0,3)^2 == 1.5).
  * T  = the fold's own stored trading days from frozen cuts.fold_day_counts
    — never a trade count, never implied.
Gate (§4b), BOTH clauses: (i) both VAL folds independently DSR >= 0.95 with
sign(SR-hat_V1) = sign(SR-hat_V2); (ii) n >= 400 long h=10 trades across the
fold set. LEADERBOARD = (i) AND (ii). Pooled-VAL DSR: DESCRIPTIVE cross-check
only, labeled, never a gate, never averaged with a per-fold number.
Fail-closed BEFORE computation (§4a): N < 30, evaluable SR-hats < 30, or
sigma_hat = 0 => NoEdgeGuard naming each reason; the caller reports NO-EDGE
with the reason beside it. Evaluable trial: >= 1 round-trip trade with
sigma_NW > 0; a dead trial is MISSING from the sigma_hat sample, never 0.
"""
from __future__ import annotations

import math
from statistics import NormalDist, mean, stdev

NW_L = 10                     # §4: bandwidth L = 10 = primary horizon
GAMMA_E = 0.5772156649        # Euler–Mascheroni, pinned constant
DSR_GATE = 0.95               # α = 0.05 stated (§3 MULTIPLE-TESTING row)
MIN_N_TRIALS = 30             # §4a fail-closed guard
MIN_EVALUABLE = 30            # §4a fail-closed guard (separate from N)
MIN_TRADES_GATE_II = 400      # §4b gate (ii): "n >= 400 trades" — long, h=10,
                              # summed across the fold set (val1 + val2) as
                              # recorded in trials.json (pinned reading)

_ND = NormalDist()            # stdlib Φ / Φ⁻¹


class NoEdgeGuard(RuntimeError):
    """§4a fail-closed guard. .reasons name every violated clause; the caller
    reports NO-EDGE for the fold with these reasons beside it."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class DsrDomainError(RuntimeError):
    """Series/denominator outside the formula's domain — fail closed, never
    silently clamp (a negative denominator² is a broken trial, not a pass)."""


def newey_west_sd(series: list[float], lags: int = NW_L) -> float:
    """Long-run SD, Newey-West HAC with Bartlett weights 1 - l/(L+1) on the
    autocovariances; 1/n scaling (stdlib only)."""
    n = len(series)
    if n < 2:
        raise DsrDomainError(f"sigma_NW needs >= 2 observations, got {n}")
    mu = mean(series)
    dev = [x - mu for x in series]
    s = sum(d * d for d in dev) / n
    for l in range(1, min(lags, n - 1) + 1):
        gamma_l = sum(dev[t] * dev[t - l] for t in range(l, n)) / n
        s += 2.0 * (1.0 - l / (lags + 1.0)) * gamma_l
    return math.sqrt(max(s, 0.0))


def _moments(series: list[float]) -> tuple[float, float]:
    """(g3, g4), g4 NON-EXCESS (Lo/Bailey): gaussian-only [1,-1,1,-1] ⇒ 1."""
    mu = mean(series)
    m2 = sum((x - mu) ** 2 for x in series) / len(series)
    if m2 == 0.0:
        return 0.0, 0.0
    m3 = sum((x - mu) ** 3 for x in series) / len(series)
    m4 = sum((x - mu) ** 4 for x in series) / len(series)
    return m3 / m2 ** 1.5, m4 / m2 ** 2


def trial_statistics(daily_series: list[float]) -> dict:
    """One trial's §4 row from its panel daily series. sigma_NW = 0 (dead
    series) leaves SR-hat None: unevaluable and MISSING from the sigma_hat
    sample, never entered as 0."""
    if len(daily_series) < 2:
        raise DsrDomainError("trial needs the fold's full daily panel "
                             f"(>= 2 days), got {len(daily_series)}")
    sigma = newey_west_sd(daily_series)
    g3, g4 = _moments(daily_series)
    m = mean(daily_series)
    return {"mean": m, "sigma_nw": sigma, "gamma3": g3, "gamma4": g4,
            "n_days": len(daily_series), "sr": (m / sigma) if sigma > 0 else None}


def sigma_hat(sr_values: list[float]) -> float:
    """Sample SD (ddof = 1) across the REALIZED trial SR-hats (§4a)."""
    return stdev(sr_values)


def sr_star(sigma_hat_value: float, n_trials: int) -> float:
    """The Bailey–López de Prado closed form, exactly as §4(a) writes it."""
    return sigma_hat_value * (
        (1.0 - GAMMA_E) * _ND.inv_cdf(1.0 - 1.0 / n_trials)
        + GAMMA_E * _ND.inv_cdf(1.0 - 1.0 / (n_trials * math.e)))


def denominator(sr: float, g3: float, g4: float) -> float:
    """sqrt(1 - g3*SR + (g4-1)/4*SR^2); g4 NON-excess (test: SR=1, g3=0,
    g4=3 => squared == 1.5)."""
    d2 = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    if d2 <= 0.0:
        raise DsrDomainError(f"denominator^2 = {d2:.6g} <= 0 at SR={sr:.6g} "
                             "(g3, g4 outside the formula's domain)")
    return math.sqrt(d2)


def dsr(sr: float, g3: float, g4: float, sigma_hat_value: float,
        n_trials: int, t_days: int) -> dict:
    """DSR = Φ(z), z = (SR - SR*)·sqrt(T-1)/denominator. The N/sigma_hat
    guards run BEFORE any computation (§4a); T comes from fold_day_counts."""
    if n_trials < MIN_N_TRIALS:
        raise NoEdgeGuard([f"N = n_trials_path {n_trials} < {MIN_N_TRIALS}"])
    if sigma_hat_value == 0.0:
        raise NoEdgeGuard(["sigma_hat = 0 (trials identical): SR* would be a "
                           "free pass — forbidden, fail closed"])
    if t_days < 2:
        raise DsrDomainError(f"fold T = {t_days} days: sqrt(T-1) undefined")
    srst = sr_star(sigma_hat_value, n_trials)
    z = (sr - srst) * math.sqrt(t_days - 1.0) / denominator(sr, g3, g4)
    return {"dsr": _ND.cdf(z), "z": z, "sr_star": srst}


def fold_guard(trials: list[dict], n_trials: int) -> list[float]:
    """§4a trio, BEFORE any DSR: N < 30, evaluable < 30, sigma_hat = 0.
    Returns the evaluable SR-hat list (the sigma_hat sample): 'evaluable' =
    a trial carrying SR-hat (≥1 round-trip trade, sigma_NW > 0); dead rows
    contribute nothing — absent, never zeros."""
    reasons = []
    if n_trials < MIN_N_TRIALS:
        reasons.append(f"N = n_trials_path {n_trials} < {MIN_N_TRIALS}")
    srs = [t["sr"] for t in trials if t.get("sr") is not None]
    if len(srs) < MIN_EVALUABLE:
        reasons.append(f"evaluable SR-hats {len(srs)} < {MIN_EVALUABLE} "
                       f"(of {len(trials)} trials on the fold)")
    if reasons:
        raise NoEdgeGuard(reasons)
    sh = sigma_hat(srs)
    if sh == 0.0:
        raise NoEdgeGuard(["sigma_hat = 0 (trials identical): SR* would be a "
                           "free pass — forbidden, fail closed"])
    return srs


def fold_dsr_table(trials: list[dict], n_trials: int, t_days: int) -> dict:
    """Per-combo DSR for one fold: guard trio first, then the T-reconciliation
    (assert trial n_days == fold T, fail closed — §4c deflates with the
    FOLD's stored trading-day count; a truncated series must never be priced
    at the manifest's T), then dsr() for every evaluable trial. Returns
    {combo: row, "_sigma_hat": sh}."""
    srs = fold_guard(trials, n_trials)
    bad = [f"{t['combo']} n_days={t['n_days']} != fold T={t_days}"
           for t in trials
           if t.get("n_days") is not None and t["n_days"] != t_days]
    if bad:
        raise DsrDomainError("series/fold-T mismatch, fail closed (§4c): "
                             + "; ".join(bad[:8]))
    sh = sigma_hat(srs)
    out: dict = {}
    for t in trials:
        if t.get("sr") is None:
            continue                          # dead trial: missing, not 0
        row = dsr(t["sr"], t["gamma3"], t["gamma4"], sh, n_trials, t_days)
        out[t["combo"]] = {**row, "sr": t["sr"], "t_days": t_days,
                           "n_trials": n_trials}
    out["_sigma_hat"] = sh
    return out


def evaluate_both_folds(trials_v1: list[dict], trials_v2: list[dict],
                        fold_day_counts: dict,
                        n_trials_by_fold: dict) -> dict:
    """§4(b) gate, BOTH clauses enforced: (i) BOTH VAL folds independently
    DSR >= 0.95 AND per-combo sign(SR-hat_V1) = sign(SR-hat_V2); (ii) n >= 400
    long h=10 trades across the fold set (val1 + val2), read from the trials
    rows (n_trades_long_h10) — a combo passing only (i) is NOT LEADERBOARD
    (DA-P1-2: the verdict word must not ship on gate (i) alone). A guard raise
    turns the whole verdict into NO-EDGE with the guard's reason returned
    beside it (never swallowed; print it wherever NO-EDGE is printed), and a
    no-qualifying verdict names WHICH gate refused: "gate-(i)" vs "gate-(ii)".
    Pooled never appears here — see pooled_descriptive()."""
    tables: dict[str, dict] = {}
    no_edge_reasons: dict[str, list[str]] = {}
    for fold, trials in (("val1", trials_v1), ("val2", trials_v2)):
        try:
            tables[fold] = fold_dsr_table(trials, n_trials_by_fold[fold],
                                          fold_day_counts[fold])
        except (NoEdgeGuard, DsrDomainError) as exc:
            no_edge_reasons[fold] = (list(exc.reasons)
                                     if isinstance(exc, NoEdgeGuard)
                                     else [str(exc)])
    if no_edge_reasons:
        return {"verdict": "NO-EDGE", "gate": DSR_GATE,
                "no_edge_reasons": no_edge_reasons, "combos": []}
    n_tr = {f: {t["combo"]: int(t.get("n_trades_long_h10", 0) or 0)
                for t in trials} for f, trials in
            (("val1", trials_v1), ("val2", trials_v2))}
    v1, v2 = tables["val1"], tables["val2"]
    combos = []
    for name in sorted(set(v1) & set(v2) - {"_sigma_hat"}):
        a, b = v1[name], v2[name]
        signs = ((a["sr"] > 0) == (b["sr"] > 0)
                 and (a["sr"] < 0) == (b["sr"] < 0)
                 and a["sr"] != 0 and b["sr"] != 0)
        n_tot = n_tr["val1"].get(name, 0) + n_tr["val2"].get(name, 0)
        gate_i = (a["dsr"] >= DSR_GATE and b["dsr"] >= DSR_GATE and signs)
        gate_ii = n_tot >= MIN_TRADES_GATE_II
        combos.append({"combo": name, "dsr_val1": a["dsr"],
                       "dsr_val2": b["dsr"], "sr_val1": a["sr"],
                       "sr_val2": b["sr"], "sign_consistent": signs,
                       "n_trades_long_h10": n_tot,
                       "gate_i_pass": gate_i, "gate_ii_pass": gate_ii,
                       "gate_pass": gate_i and gate_ii})
    qualifying = [c["combo"] for c in combos if c["gate_pass"]]
    if not qualifying and combos:
        if any(c["gate_i_pass"] for c in combos):
            fails = [f"{c['combo']} n={c['n_trades_long_h10']}"
                     for c in combos
                     if c["gate_i_pass"] and not c["gate_ii_pass"]]
            no_edge_reasons["gate"] = [
                f"gate-(ii) refused: needs n >= {MIN_TRADES_GATE_II} long "
                f"h=10 trades across val1+val2; passing gate-(i) but short: "
                + ", ".join(fails[:8])]
        else:
            no_edge_reasons["gate"] = [
                f"gate-(i) refused: no combo reached DSR >= {DSR_GATE} on "
                "BOTH folds independently with sign consistency"]
    return {"verdict": "LEADERBOARD" if qualifying else "NO-EDGE",
            "gate": DSR_GATE, "qualifying": qualifying, "combos": combos,
            "no_edge_reasons": no_edge_reasons,
            "sigma_hat": {"val1": v1["_sigma_hat"], "val2": v2["_sigma_hat"]},
            "n_trials_path": dict(n_trials_by_fold),
            "fold_day_counts": {k: fold_day_counts[k] for k in ("val1", "val2")}}


def pooled_descriptive(trials_pooled: list[dict], n_trials: int,
                       t_days_pooled: int) -> dict:
    """§4(b): the pooled-VAL DSR is a DESCRIPTIVE cross-check only — labeled,
    never averaged with a per-fold number, never a gate. Guarded identically
    (a pooled guard failure reads 'descriptive unavailable', not NO-EDGE)."""
    try:
        table = fold_dsr_table(trials_pooled, n_trials, t_days_pooled)
    except (NoEdgeGuard, DsrDomainError) as exc:
        return {"descriptive_only": True, "unavailable": True,
                "reasons": (list(exc.reasons) if isinstance(exc, NoEdgeGuard)
                            else [str(exc)])}
    rows = {k: {**v, "descriptive_only": True}
            for k, v in table.items() if k != "_sigma_hat"}
    return {"descriptive_only": True, "unavailable": False,
            "sigma_hat": table["_sigma_hat"], "combos": rows}
