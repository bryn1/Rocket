"""score_all (MC 10222; TWO-PASS wire law MC 11474 B2): picklable Pool worker,
ONE CSV per call, zero frame accumulation, page-shape rows via the island
adapter (nightly_score.score_row), capped journal (F9) — plus the two-pass
merge law (C3) and the once-per-run index feed (C1). The Pool(2) test is the
honest picklability proof (real island import in worker init — the nightly
path), so it may take a few seconds.

Ownership note (B2): this module rides the B2 commit as the DIRECT CONSEQUENCE
file of the score_all.py hunk (B0's test_store_lock.py precedent) — the task
tuple gains the explicit social flag (PLAN-c3 §1), so the worker calls here
move with it; no wave row owns this file.
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
from full_universe import ALLA_CAP, UniversePlan, cache_filename  # noqa: E402

TODAY_ROW_KEYS = {"ticker", "signal", "overall", "momentum", "trend",
                  "volatility", "volume", "close"}


@pytest.fixture(autouse=True)
def _hermetic_social(monkeypatch):
    """Pass-2 social=True must not touch the network here: the sentiment
    scrape posts to localhost:8000 (D2 shape). Refuse instantly — the island
    fails neutral (social 0.0), exactly how this host behaves with no
    service (B1's pin). Pool workers inherit the patch via fork."""
    import httpx

    def refuse(*a, **k):
        raise httpx.ConnectError("planted hermetic refusal (no sentiment service)")
    monkeypatch.setattr(httpx, "post", refuse)


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
    task = ("A", "usa", "/tmp/whatever", False)     # social EXPLICIT (D2)
    assert pickle.loads(pickle.dumps(task)) == task


def test_worker_scores_real_csv_today_shape(tmp_path):
    _store(tmp_path, "GOOD", "usa", days=45)
    meta = score_all._score_one(("GOOD", "usa", str(tmp_path), False))
    assert meta["row"] is not None
    assert set(meta["row"]) == TODAY_ROW_KEYS        # demo_render._table shape
    assert meta["row"]["ticker"] == "GOOD"
    assert meta["last_bar"] == str(fx.today())
    assert meta["has_usable_store"] and not meta["error"]
    # NO FRAME ESCAPES THE WORKER:
    assert not any(isinstance(v, pd.DataFrame) for v in meta.values())


def test_worker_thin_missing_and_broken_are_metas_not_raises(tmp_path):
    _store(tmp_path, "THIN", "usa", days=5)
    thin = score_all._score_one(("THIN", "usa", str(tmp_path), False))
    assert thin["row"] is None and thin["has_usable_store"]  # -> insufficient
    missing = score_all._score_one(("GHOST", "usa", str(tmp_path), False))
    assert missing["row"] is None and not missing["has_usable_store"]
    (tmp_path / cache_filename("BAD")).write_bytes(b"")
    broken = score_all._score_one(("BAD", "usa", str(tmp_path), False))
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


# ── TWO-PASS merge law (D2/C3, MC 11474 B2 DoD planted-bads) ───────────────
def _fake_adapter(overall_of, *, bump=10.0, fail_on=()):
    """Deterministic score_row stand-in: page-shape rows with overalls from a
    fixed map; the social=True leg bumps by `bump` so a pass-1 row and a
    pass-2 row are DISTINGUISHABLE; tickers in fail_on raise ONLY on pass 2.
    Calls recorded: (ticker, social, index_df_is_none)."""
    calls: list[tuple] = []

    def score_row(df, ticker, region, *, social, index_df=None,
                  index_name="^GSPC"):
        calls.append((ticker, social, index_df is None))
        if social and ticker in fail_on:
            raise RuntimeError("planted: pass-2 adapter death")
        return {"ticker": ticker, "signal": "HOLD",
                "overall": overall_of[ticker] + (bump if social else 0.0),
                "momentum": 50.0, "trend": 50.0, "volatility": 50.0,
                "volume": 50.0, "close": 1.0}
    return score_row, calls


def test_pass2_single_ticker_error_keeps_pass1_row_error_count_1(
        tmp_path, monkeypatch):
    """C3 planted-bad (B2 DoD): pass-2 raises on exactly ONE top ticker ⇒
    its row is present with the PASS-1 value and error_count is 1. The bump
    stand-in proves which pass a row came from; other tickers DO carry their
    pass-2 rows (replacement happened for them)."""
    import nightly_score
    for t in ("A1", "A2", "A3"):
        _store(tmp_path, t, "usa")
    plan = _plan(usa=["A1", "A2", "A3"])
    fake, calls = _fake_adapter({"A1": 70.0, "A2": 60.0, "A3": 50.0},
                                fail_on={"A2"})
    monkeypatch.setattr(nightly_score, "score_row", fake)
    out = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    rows = {r["ticker"]: r["overall"] for r in out["results"]["usa"]}
    assert rows["A2"] == 60.0                      # pass-1 value KEPT
    assert rows["A1"] == 80.0 and rows["A3"] == 60.0   # pass-2 rows (bumped)
    assert out["meta"]["A2"]["row"] is not None    # row not lost
    assert "pass-2 adapter death" in out["meta"]["A2"]["error"]
    assert out["scalars"]["error_count"] == 1      # exactly one error rides
    tickers = [r["ticker"] for r in out["results"]["usa"]]
    assert len(tickers) == len(set(tickers)) == 3  # no ticker twice, none lost
    assert [t for t, s, _ in calls if s] == ["A1", "A2", "A3"]


def test_two_pass_zero_duplicates_at_alla_cap_scale(tmp_path, monkeypatch):
    """C3 planted-bad GREEN half (B2 DoD): pass-2 fully green ⇒ ZERO
    duplicate tickers AT ALLA_CAP SCALE — 510 planned, cap = the real
    ALLA_CAP (500, full_universe.py:60), none loses its scored pass-1 row;
    and pass 2 fires for EXACTLY the top ALLA_CAP keys by preliminary
    overall."""
    import nightly_score
    assert ALLA_CAP == 500
    names = [f"S{i:04d}" for i in range(ALLA_CAP + 10)]     # 510 unique
    overalls = {t: float(10000 - i) for i, t in enumerate(names)}
    for t in names:
        _store(tmp_path, t, "usa")
    plan = _plan(usa=names)
    fake, calls = _fake_adapter(overalls)
    monkeypatch.setattr(nightly_score, "score_row", fake)
    out = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    tickers = [r["ticker"] for r in out["results"]["usa"]]
    assert len(tickers) == len(set(tickers)) == ALLA_CAP + 10   # zero dups
    rows = {r["ticker"]: r["overall"] for r in out["results"]["usa"]}
    top = set(names[:ALLA_CAP])                    # highest preliminaries
    assert {t for t, s, _ in calls if s} == top    # exactly top ALLA_CAP
    assert all(rows[t] == overalls[t] + 10.0 for t in top)      # pass-2 rows
    assert all(rows[t] == overalls[t] for t in set(names) - top)  # pass-1 rows
    assert out["scalars"]["error_count"] == 0


# ── C1 index feed (B2 DoD: once per run, explicit per call, real effect) ───
def _bull_index_csv(path):
    """Deterministic BULL index frame (260 rising bars — the store V6 shape):
    detect_regime lands BULL×1.0, provably NOT the CHOP×0.5 the missing-file
    fallback takes (asserted in-test before the differ is credited)."""
    n = 260
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = [100.0 * (1.004 ** i) for i in range(n)]
    pd.DataFrame({"date": idx, "open": close, "high": [c * 1.004 for c in close],
                  "low": [c * 0.996 for c in close], "close": close,
                  "volume": [1e6] * n}).to_csv(path, index=False)


def test_index_feed_loaded_once_and_passed_every_call(tmp_path, monkeypatch):
    """C1 (folded verbatim): score_all() loads the index CSVs ONCE per run
    and passes index_df + index_name to EVERY score_row call — no
    default-None wiring on the live path. The covered (usa) ticker arrives
    with a real frame + its REGION_INDEX name; an UNCOVERED region (japan)
    arrives with None — the D1 honest fallback, kept, not regressed."""
    import nightly_score
    _store(tmp_path, "A1", "usa")
    _store(tmp_path, "J1", "japan")
    _bull_index_csv(tmp_path / cache_filename("^GSPC"))
    plan = _plan(usa=["A1"], japan=["J1"])

    seen: list[dict] = []
    real = nightly_score.score_row

    def spy(df, ticker, region, *, social, index_df, index_name):
        # keyword-for-keyword: a driver that omits index_df/index_name dies
        # here with TypeError — the "EVERY call" half of the C1 law.
        seen.append({"ticker": ticker, "social": social,
                     "index_df": index_df, "index_name": index_name})
        return real(df, ticker, region, social=social,
                    index_df=index_df, index_name=index_name)

    loads: list[int] = []
    real_load = score_all._load_indices

    def counting_load(cache_dir=None):
        loads.append(1)
        return real_load(cache_dir)

    monkeypatch.setattr(nightly_score, "score_row", spy)
    monkeypatch.setattr(score_all, "_load_indices", counting_load)
    out = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    assert out["scalars"]["error_count"] == 0
    assert len(loads) == 1                     # ONCE per run, not per pass
    assert len(seen) == 4                      # 2 tickers x both passes
    for call in seen:
        if call["ticker"] == "A1":
            assert call["index_df"] is not None
            assert call["index_name"] == "^GSPC"    # REGION_INDEX's name
        else:
            assert call["index_df"] is None         # honest fallback
    assert [c["social"] for c in seen].count(False) == 2   # pass 1
    assert [c["social"] for c in seen].count(True) == 2    # pass 2


def test_usa_ticker_scores_differ_with_vs_without_index_csv(tmp_path):
    """C1 end-to-end (B2 DoD planted): the SAME usa ticker and SAME store
    bytes score DIFFERENTLY with vs without the fetched index CSV present —
    the non-CHOP path is reached through score_all, not only in B0's unit."""
    from rocket.backtest.indicator_eval import to_indicator_frame
    from rocket.technical.regime import detect_regime
    _store(tmp_path, "A1", "usa", days=60)
    plan = _plan(usa=["A1"])
    without = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    _bull_index_csv(tmp_path / cache_filename("^GSPC"))
    # the fixture reaches a NON-CHOP regime, or the differ below is hollow:
    assert detect_regime(to_indicator_frame(
        store_io.read_cache(tmp_path / cache_filename("^GSPC"))),
        "^GSPC").multiplier == 1.0                    # BULL×1.0, not 0.5
    with_idx = score_all.score_all(plan, cache_dir=tmp_path, workers=0)
    a = without["results"]["usa"][0]["overall"]
    b = with_idx["results"]["usa"][0]["overall"]
    assert a != b, (a, b)
