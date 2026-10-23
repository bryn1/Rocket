"""C3 (MC 11186, re-audit d3 F2): the §9 publish floors must mean strictly
>= their fraction. round() is banker's rounding, and BOTH floors in
generate_demo_page._guard used it — the scored floor (0.40 x m_unique) and
the G6 per-region floor (0.50 x n_r) — so the guards failed OPEN at
49.66-49.99 % (d3 F2 accepted live shapes: 3012/6025, 744/1489, 146/293,
72/145). This file pins the exact CEIL boundary for both floors on both
sides: just-below FAILS, at-boundary PASSES (the >= semantics pinned by
test_guard_scored_floor_equality_green_and_below_red survive the ceil
change). The just-below rows ARE the planted-bad reds: under pre-fix
round() they pass the guard and this file goes red (proof captured in the
worktree .tmp/ pre-fix run). Generator loaded via the shared dtgen seam
(test_full_universe_pipeline idiom); no source/data needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import full_universe  # noqa: E402
import store_io  # noqa: E402
import test_demo_page_generator as dtgen  # noqa: E402 (shared gen load)
from demo_page_fixtures import FRESH_BAR, iso, mk_row  # noqa: E402

gen = dtgen.gen
NOW = dtgen.NOW


def _plan(sizes: dict[str, int]):
    """Boundary plan: one region of each named size, m_unique = sum —
    the guards read ONLY plan.regions/order/m_unique, so no registry file
    is involved (DoD: hermetic, no source/data)."""
    regions = {r: [f"{r}-{i}" for i in range(n)] for r, n in sizes.items()}
    return full_universe.UniversePlan(
        regions=regions, order=list(sizes), m_unique=sum(sizes.values()))


def _guard(monkeypatch, plan, scored_counts: dict[str, int], **floors):
    """Run the REAL gen._guard green-except-where-scored: all markers
    present, partition closed, fresh settled bar. floors patches the §9
    constants (fixtures idiom). Returns the reasons list."""
    monkeypatch.setattr(gen, "HARD_SCORED_FLOOR", 1)
    for name, value in floors.items():
        monkeypatch.setattr(gen, name, value)
    scored_by_region = {r: [mk_row(t) for t in plan.regions[r][:n]]
                        for r, n in scored_counts.items()}
    assignments = {t: store_io.SCORED for r in plan.order
                   for t in plan.regions[r]}
    html = "".join(gen._markers(plan))  # every required marker, nothing else
    return gen._guard(html, plan, assignments, scored_by_region,
                      FRESH_BAR, iso(NOW), FRESH_BAR)


# ── helper: exact integer ceil, both production fractions ──────────────────

def test_ceil_floor_exact_on_boundary_values():
    # 40 % floor: odd m rounds 4333 (fail-open), ceil is 4334; the exact
    # 40 % point 4332 must stay 4332 (no float overshoot — Fraction read-back).
    assert gen._ceil_floor(0.4, 10833) == 4334
    assert gen._ceil_floor(0.4, 10830) == 4332
    # 50 % G6: odd n at .5 rounded DOWN pre-fix (3012/72/500), ceil is up;
    # the exact-half 1002 stays 501.
    assert gen._ceil_floor(0.5, 6025) == 3013
    assert gen._ceil_floor(0.5, 145) == 73
    assert gen._ceil_floor(0.5, 1001) == 501
    assert gen._ceil_floor(0.5, 1002) == 501
    assert gen._ceil_floor(0.0, 10833) == 0


# ── G6 per-region floor: exact 50 % ceil boundary, both sides ───────────────
# (region, scored, expected_g6_floor) — the d3 F2 live shapes first:
# pre-fix round() accepted every just-below row here (planted-bad reds).

G6_CASES = [(6025, 3012, 3013), (6025, 3013, 3013),    # 49.99 % red / pass
            (1489, 744, 745), (1489, 745, 745),
            (293, 146, 147), (293, 147, 147),
            (145, 72, 73), (145, 73, 73),               # 49.66 % red / pass
            (1001, 500, 501), (1001, 501, 501),         # round(500.5)=500 red
            (1002, 500, 501), (1002, 501, 501),         # exact half: 501 PASS
            (5, 2, 3), (5, 3, 3), (9, 4, 5), (9, 5, 5)]  # d3 P2.2a-b shapes


@pytest.mark.parametrize("n,s,floor", G6_CASES)
def test_g6_floor_exact_ceil_boundary(monkeypatch, n, s, floor):
    plan = _plan({"boundary": n, "filler": 5000})
    reasons = _guard(monkeypatch, plan, {"boundary": s, "filler": 5000})
    g6 = [r for r in reasons if "G6 region boundary" in r]
    if s >= floor:                                    # at-boundary / above
        assert reasons == [] and g6 == []
    else:                                             # planted-bad: just below
        assert len(g6) == 1 and f"scorerade {s} av {n} (golv >= {floor})" \
            in g6[0]


def test_g6_even_region_total_below_red_at_floor_green(monkeypatch):
    # brief's honest n=1002 row: 501/1002 is EXACTLY 50.00 % (not 49.99),
    # so at 501 it must PASS and one less must FAIL — ceil boundary pinned.
    plan = _plan({"boundary": 1002, "filler": 9831})  # m=10833: floor 4334
    assert _guard(monkeypatch, plan,
                  {"boundary": 501, "filler": 9831}) == []
    reasons = _guard(monkeypatch, plan, {"boundary": 500, "filler": 9831})
    assert any("G6 region boundary: scorerade 500 av 1002 (golv >= 501)"
               in r for r in reasons)


# ── §9 #1 scored floor: exact 40 % ceil boundary, both sides ────────────────
# G6_FRACTION patched to 0.0 (existing floors_patched idiom) so the ONLY
# live floor is the scored floor; every region still carries >= 1 row.

def test_scored_floor_odd_m_unique_ceil_boundary(monkeypatch):
    plan = _plan({"usa": 5000, "japan": 3000, "sweden": 2833})   # m=10833
    assert gen._ceil_floor(0.4, plan.m_unique) == 4334  # 4333.2 -> 4334
    below = _guard(monkeypatch, plan,
                   {"usa": 4331, "japan": 1, "sweden": 1},
                   SCORED_FLOOR_FRACTION=0.4, G6_FRACTION=0.0)
    assert "endast 4333 scorerade rader (golv 4334)" in below   # planted red
    assert _guard(monkeypatch, plan,
                  {"usa": 4332, "japan": 1, "sweden": 1},
                  SCORED_FLOOR_FRACTION=0.4, G6_FRACTION=0.0) == []


def test_scored_floor_exact_40_percent_point_not_overshot(monkeypatch):
    # m=10830: 0.4 x 10830 = 4332 EXACT — ceil must be 4332 (>= promises
    # 4332 passes), and 4331 (39.99 %) must fail.
    plan = _plan({"usa": 5000, "japan": 3000, "sweden": 2830})   # m=10830
    below = _guard(monkeypatch, plan,
                   {"usa": 4329, "japan": 1, "sweden": 1},   # total 4331
                   SCORED_FLOOR_FRACTION=0.4, G6_FRACTION=0.0)
    assert "endast 4331 scorerade rader (golv 4332)" in below
    assert _guard(monkeypatch, plan,
                  {"usa": 4330, "japan": 1, "sweden": 1},    # total 4332
                  SCORED_FLOOR_FRACTION=0.4, G6_FRACTION=0.0) == []
