"""sample_backtest (MC 10222): deterministic stratified sampler (§8), chunk
contract, the BOUNDED merge assertion (cycle-1's blanket assert is dead:
generated_at/engine differ per chunk BY CONSTRUCTION and must NOT red the
merge), additive sample block, and the sample's universe embedded."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fu_fixtures as fx  # noqa: E402
import sample_backtest as sb  # noqa: E402
from full_universe import UniversePlan, cache_filename  # noqa: E402


def _live(n_by_region):
    return {r: [f"{r}{i:04d}" for i in range(n)] for r, n in n_by_region.items()}


# ── sampler: pure, deterministic, quota'd ──────────────────────────────────
def test_sampler_identical_input_twice():
    live = _live({"usa": 6000, "sweden": 700, "hongkong": 12, "china": 0})
    a = sb.stratified_sample(live)
    b = sb.stratified_sample(live)
    assert a == b                                   # pure fn, no RNG, nothing to seed
    assert "china" not in a                         # empty region contributes nothing


def test_sampler_quotas_and_representatives():
    live = _live({"usa": 480, "hongkong": 10, "sweden": 10})
    s = sb.stratified_sample(live, total=500)       # n_live=500 => exact quotas
    assert len(s["usa"]) == 480 and len(s["hongkong"]) == 10  # round() keeps 10
    live = _live({"usa": 1000, "hongkong": 3})
    s = sb.stratified_sample(live, total=100)
    assert sb.sample_size(s) <= 100
    assert len(s["hongkong"]) >= 1                  # max(1, ...) keeps thin regions
    # fixed stride on the SORTED list: quota 4 of 10 -> indices 0,2,4,6
    picked = sb.stratified_sample({"r": [f"T{i:02d}" for i in range(10)]}, total=4)
    assert picked["r"] == ["T00", "T02", "T04", "T06"]


def test_sampler_hard_cap():
    live = {f"reg{i:03d}": [f"T{i}"] for i in range(600)}   # 600 singleton regions
    s = sb.stratified_sample(live, total=500)
    assert sb.sample_size(s) <= 500


def test_sample_block_contract():
    assert sb.sample_block(500) == {"tickers": 500,
                                    "method": "deterministic-stratified",
                                    "cadence": "weekly"}


# ── chunking ───────────────────────────────────────────────────────────────
def test_indicator_chunks_deterministic_and_disjoint():
    c1 = sb.indicator_chunks(12)
    c2 = sb.indicator_chunks(12)
    assert c1 == c2
    flat = [n for c in c1 for n in c]
    assert len(flat) == len(set(flat)) == 34
    assert flat == sorted(flat)
    assert len(c1) == 12


# ── merge: bounded assertion (the §8 fix) ──────────────────────────────────
def _chunk_doc(**over):
    doc = {"schema_version": 1, "generated_at": "2026-10-03T05:00:00Z",
           "data_last_bar": str(fx.today()),
           "engine": {"indicators_registered": 3, "tickers": 500,
                      "warmup_bars": 60},
           "universe": {"usa": ["A"]}, "costs_pct": {"roundtrip": 0.3},
           "horizons_days": [5, 10, 20], "primary_horizon_days": 10,
           "entry_rule": "edge-triggered", "incomplete_dropped": {"long": 1, "short": 2},
           "indicators": {"ADX": {"n": 1}, "ATR": {"n": 2}}}
    doc.update(over)
    return doc


def test_merge_accepts_per_chunk_construction_differences():
    """generated_at + engine differ per chunk BY CONSTRUCTION (VERIFIED §8).
    The bounded assert list must let them pass, or every Saturday goes red."""
    d1 = _chunk_doc(generated_at="2026-10-03T05:00:01Z",
                    engine={"indicators_registered": 3, "tickers": 500,
                            "warmup_bars": 60})
    d2 = _chunk_doc(generated_at="2026-10-03T05:00:09Z",
                    engine={"indicators_registered": 1, "tickers": 500,
                            "warmup_bars": 60},
                    indicators={"BOLL": {"n": 3}})
    for k in sb.MERGE_ASSERT_KEYS:
        assert k not in ("generated_at", "engine")
    merged = sb.merge_chunk_docs([d1, d2], sample_size_n=500,
                                 now_fn=lambda: "MERGED-STAMP")
    assert merged["generated_at"] == "MERGED-STAMP"          # set at merge
    assert merged["engine"] == {"indicators_registered": 3,
                                "tickers": 500, "warmup_bars": 60}
    assert sorted(merged["indicators"]) == ["ADX", "ATR", "BOLL"]
    assert merged["incomplete_dropped"] == {"long": 2, "short": 4}
    assert merged["sample"] == sb.sample_block(500)


def test_merge_reds_on_asserted_field_names_key():
    """PLANTED RED (bounded, §8): a chunk disagreeing on data_last_bar must
    raise AND name the field."""
    d1, d2 = _chunk_doc(), _chunk_doc(data_last_bar="1999-01-01")
    with pytest.raises(sb.SampleBacktestError, match="data_last_bar"):
        sb.merge_chunk_docs([d1, d2], sample_size_n=1)
    with pytest.raises(sb.SampleBacktestError, match="schema_version"):
        sb.merge_chunk_docs([_chunk_doc(), _chunk_doc(schema_version=2)],
                            sample_size_n=1)
    with pytest.raises(sb.SampleBacktestError, match="no chunk docs"):
        sb.merge_chunk_docs([], sample_size_n=1)


# ── full inline run through the REAL eval seam ─────────────────────────────
def _tiny_store(tmp_path, names):
    for n in names:
        fx.mk_frame(80).to_csv(tmp_path / cache_filename(n), index=False)


def test_run_sample_backtest_real_eval_inline(tmp_path):
    _tiny_store(tmp_path, ["A", "B", "C"])
    plan = UniversePlan(regions={"usa": ["A", "B"], "hongkong": ["C"]},
                        order=["usa", "hongkong"], m_unique=3)
    doc = sb.run_sample_backtest(plan, cache_dir=tmp_path, workers=0,
                                 sample_size_total=3, now_fn=lambda: "STAMP")
    assert doc["schema_version"] == 1
    assert doc["generated_at"] == "STAMP"
    assert len(doc["indicators"]) == 34             # all indicators across chunks
    assert doc["engine"]["indicators_registered"] == 34   # derived, never literal-pinned here
    assert doc["engine"]["tickers"] == 3
    assert doc["sample"] == sb.sample_block(3)
    assert doc["universe"] == {"hongkong": ["C"], "usa": ["A", "B"]}  # SAMPLE's universe
    assert sb.sample_size(doc["universe"]) <= 500


def test_run_sample_backtest_eval_seam_receives_chunks(tmp_path):
    _tiny_store(tmp_path, ["A"])
    seen = []

    def fake_eval(names, members, cdir):
        seen.append(tuple(names))
        return _chunk_doc(indicators={n: {"n": 0} for n in names})

    plan = UniversePlan(regions={"usa": ["A"]}, order=["usa"], m_unique=1)
    doc = sb.run_sample_backtest(plan, cache_dir=tmp_path,
                                 sample_size_total=1, eval_fn=fake_eval,
                                 now_fn=lambda: "S")
    flat = sorted(n for c in seen for n in c)
    assert len(flat) == len(set(flat)) == 34        # every indicator, exactly one chunk
    assert len(doc["indicators"]) == 34


def test_run_sample_backtest_empty_store_reds():
    plan = UniversePlan(regions={"usa": ["A"]}, order=["usa"], m_unique=1)
    with pytest.raises(sb.SampleBacktestError, match="no readable frames"):
        sb.run_sample_backtest(plan, cache_dir=Path("/nonexistent-store"),
                               workers=0, sample_size_total=1)
