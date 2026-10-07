"""score_all (MC 10222): picklable Pool worker, ONE CSV per call, zero frame
accumulation, today-shape rows via the real app seam, capped journal (F9).
The Pool(2) test is the honest picklability proof (real app import in worker
init — the nightly path), so it may take a few seconds.
"""
import pickle
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fu_fixtures as fx  # noqa: E402
import score_all  # noqa: E402
import store_io  # noqa: E402
from full_universe import UniversePlan, cache_filename  # noqa: E402

TODAY_ROW_KEYS = {"ticker", "signal", "overall", "momentum", "trend",
                  "volatility", "volume", "close"}


def _store(tmp_path, ticker, region, days=60):
    df = fx.mk_frame(days)
    df.to_csv(tmp_path / cache_filename(ticker), index=False)
    return df


def _plan(**regions):
    return UniversePlan(regions={r: list(ts) for r, ts in regions.items()},
                        order=list(regions),
                        m_unique=sum(len(v) for v in regions.values()))


# ── worker contract ────────────────────────────────────────────────────────
def test_worker_module_level_and_picklable():
    assert pickle.dumps(score_all._score_one)
    assert pickle.dumps(score_all._init_worker)
    task = ("A", "usa", "/tmp/whatever")
    assert pickle.loads(pickle.dumps(task)) == task


def test_worker_scores_real_csv_today_shape(tmp_path):
    _store(tmp_path, "GOOD", "usa", days=45)
    meta = score_all._score_one(("GOOD", "usa", str(tmp_path)))
    assert meta["row"] is not None
    assert set(meta["row"]) == TODAY_ROW_KEYS        # demo_render._table shape
    assert meta["row"]["ticker"] == "GOOD"
    assert meta["last_bar"] == str(fx.today())
    assert meta["has_usable_store"] and not meta["error"]
    # NO FRAME ESCAPES THE WORKER:
    assert not any(isinstance(v, pd.DataFrame) for v in meta.values())


def test_worker_thin_missing_and_broken_are_metas_not_raises(tmp_path):
    _store(tmp_path, "THIN", "usa", days=5)
    thin = score_all._score_one(("THIN", "usa", str(tmp_path)))
    assert thin["row"] is None and thin["has_usable_store"]  # -> insufficient
    missing = score_all._score_one(("GHOST", "usa", str(tmp_path)))
    assert missing["row"] is None and not missing["has_usable_store"]
    (tmp_path / cache_filename("BAD")).write_bytes(b"")
    broken = score_all._score_one(("BAD", "usa", str(tmp_path)))
    assert broken["row"] is None and not broken["has_usable_store"]
    assert not broken["error"]          # corrupt heals via store, not journal


# ── score_all() over a plan ────────────────────────────────────────────────
def test_score_all_inline_shape(tmp_path):
    _store(tmp_path, "A1", "usa")
    _store(tmp_path, "A2", "usa")
    _store(tmp_path, "H1", "hongkong")
    _store(tmp_path, "THIN", "hongkong", days=4)
    plan = _plan(usa=["A1", "A2", "GONE"], hongkong=["H1", "THIN"])
    out = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    assert sorted(r["ticker"] for r in out["results"]["usa"]) == ["A1", "A2"]
    assert [r["ticker"] for r in out["results"]["hongkong"]] == ["H1"]
    assert out["meta"]["GONE"]["has_usable_store"] is False
    assert out["meta"]["THIN"]["has_usable_store"] is True
    assert out["scalars"]["scored_regions"] == {"usa": 2, "hongkong": 1}
    assert out["scalars"]["scored_total"] == 3
    assert out["last_bars"]["usa"] and all(
        lb == str(fx.today()) for lb in out["last_bars"]["usa"])
    # partition feeds straight out of meta (§4):
    cls = {t: store_io.classify_accounting(
        produced_row=m["row"] is not None, batch_completed=True,
        has_usable_store=m["has_usable_store"])
        for t, m in out["meta"].items()}
    assert cls["GONE"] == store_io.DEAD_AT_FETCH
    assert cls["THIN"] == store_io.INSUFFICIENT_ROWS
    assert cls["A1"] == store_io.SCORED


@pytest.mark.slow
def test_score_all_real_pool2_matches_inline(tmp_path):
    """The nightly mechanism for real: multiprocessing Pool(2), initializer
    imports app per worker (fork), tasks/returns pickle cleanly."""
    for t in ("P1", "P2", "P3", "P4"):
        _store(tmp_path, t, "usa")
    plan = _plan(usa=["P1", "P2", "P3", "P4"])
    pooled = score_all.score_all(plan, cache_dir=tmp_path, workers=2)
    inline = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    assert {r["ticker"]: r["overall"]
            for r in pooled["results"]["usa"]} == \
           {r["ticker"]: r["overall"] for r in inline["results"]["usa"]}
    assert pooled["scalars"]["scored_total"] == 4


# ── F9 journal cap ─────────────────────────────────────────────────────────
def test_journal_cap_first_10_only():
    errors = [f"T{i}: boom" for i in range(25)]
    scalars = {"attempted": 25, "scored_regions": {}, "scored_total": 0,
               "error_count": 25, "errors_shown": errors[:score_all.ERROR_LOG_CAP],
               "errors_truncated": 25 - score_all.ERROR_LOG_CAP}
    line = score_all.summarize(scalars)
    assert "errors=25" in line and "(+15 more)" in line
    assert "T10: boom" not in line        # 11th never printed
    assert "T9: boom" in line             # 10th shown
    # and score_all itself never ships more than the cap into scalars:
    assert score_all.ERROR_LOG_CAP == 10
