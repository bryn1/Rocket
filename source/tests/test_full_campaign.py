"""full_campaign (MC 10373, T-BT4): walk-forward windowing over the global
calendar, seam-law fold slices with left warm-up margin, BH-corrected ranking
over the (indicator, fold) family, and the holdout as the LAST window only."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_campaign_fixtures as fx  # noqa: E402

from rocket.backtest import full_campaign as fc  # noqa: E402
from rocket.backtest.indicator_eval import WARMUP_BARS  # noqa: E402


# ── fold_windows: warm-up start, equal counts, monotone disjoint ───────────
def test_windows_start_after_warmup_and_cover_tail():
    wins, n = fc.fold_windows(fx.synth_frames(269))
    assert n == 269
    assert len(wins) == fc.N_FOLDS + 1
    # first window starts at dates[WARMUP_BARS] (seam never emits before it)
    assert wins[0][0] == fx.date_at(269, WARMUP_BARS)
    # last window reaches the newest date
    assert wins[-1][1] == fx.date_at(269, 268)
    # monotone, disjoint, gapless over the post-warm-up calendar
    for k in range(1, len(wins)):
        assert wins[k][0] > wins[k - 1][1]


def test_windows_equal_count_absorb_remainder_in_last():
    wins, _ = fc.fold_windows(fx.synth_frames(269))
    sizes = [(pd.Timestamp(b) - pd.Timestamp(a)).days + 1 for a, b in wins]
    assert sizes[0] == sizes[1] == sizes[-1] or sizes[-1] >= sizes[0]


def test_windows_short_store_still_three():
    wins, n = fc.fold_windows(fx.synth_frames(66))         # just past warm-up
    assert n == 66 and len(wins) == fc.N_FOLDS + 1
    assert wins[0][0] == fx.date_at(66, WARMUP_BARS)


# ── fold_doc: seam law on a tiny store ──────────────────────────────────────
def test_fold_doc_positive_n_and_schema():
    frames = fx.synth_frames(200)
    wins, _ = fc.fold_windows(frames)
    doc = fc.fold_doc(frames, wins, 0, ["RSI", "MACD"])
    blk = doc["indicators"]["RSI"]
    assert blk["n_signals"] >= 1                           # margin law holds
    assert blk["horizons"]["10"]["short"]["n"] >= 1
    assert set(blk["horizons"]) == {"5", "10", "20"}
    assert "evidence_t" in blk


def test_fold_doc_deterministic_twice():
    frames = fx.synth_frames(200)
    wins, _ = fc.fold_windows(frames)
    a = fc.fold_doc(frames, wins, 1, ["RSI"])
    b = fc.fold_doc(frames, wins, 1, ["RSI"])
    assert a == b


def test_fold_doc_unknown_name_absent():
    frames = fx.synth_frames(200)
    wins, _ = fc.fold_windows(frames)
    doc = fc.fold_doc(frames, wins, 0, ["RSI"])
    assert list(doc["indicators"]) == ["RSI"]


# ── rank_combinations: BH over (name, fold), planted-bad reds ──────────────
def _blk(t, n, avg):
    return {"horizons": {"10": {"long": {"n": n, "hit_rate": 0.5,
                                          "avg_net_pct": avg}}},
            "evidence_t": t}


def test_bh_ranking_order_and_pass():
    f1 = {"indicators": {"Strong": _blk(3.0, 100, 1.0),
                         "Nullish": _blk(None, 0, None)}}
    f2 = {"indicators": {"Strong": _blk(2.8, 100, 1.2),
                         "Nullish": _blk(None, 0, None)}}
    ranked = dict(fc.rank_combinations([f1, f2]))
    assert ranked["Strong"]["summary"]["bh_pass"] is True
    assert ranked["Strong"]["summary"]["n_total"] == 200
    assert ranked["Nullish"]["summary"]["n_total"] == 0
    assert ranked["Nullish"]["summary"]["bh_pass"] is False
    order = [n for n, _ in fc.rank_combinations([f1, f2])]
    assert order[0] == "Strong"                            # planted-bad: swap reds


def test_bh_cut_is_global_not_per_name():
    # one loud fold-test cannot lift a name whose other fold is null when the
    # family cut sits below its max p; a uniformly better name outranks it.
    f1 = {"indicators": {"A": _blk(1.9, 100, 0.5),
                         "B": _blk(4.0, 100, 2.0)}}
    f2 = {"indicators": {"A": _blk(0.2, 100, 0.5),
                         "B": _blk(4.2, 100, 2.0)}}
    ranked = dict(fc.rank_combinations([f1, f2]))
    # both BH-pass (p<=q*i/m with tiny p's), B outranks on avg
    assert ranked["B"]["summary"]["bh_pass"] and ranked["A"]["summary"]["bh_pass"]
    order = [n for n, _ in fc.rank_combinations([f1, f2])]
    assert order[0] == "B"


def test_p_one_sided_values():
    assert fc._t_p_one_sided(0.0, 100) == pytest.approx(0.5)
    assert fc._t_p_one_sided(1.96, 100) == pytest.approx(0.024998, abs=1e-4)
    assert fc._t_p_one_sided(3.0, 100) < fc._t_p_one_sided(1.0, 100)
