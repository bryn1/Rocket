"""full_universe (MC 10222): loader contract, S0 floors, seam C4, C3-F1
stride batching. Fixture registry per DESIGN §9 G4 via fu_fixtures; REAL
registry is exercised READ-ONLY (no network) for the §2/§7 digit pins.

Why past 250 lines: one subject (the loader+batching contract) and the
planted-bad C3-F1 pair must live in the same module as its green twins.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fu_fixtures as fx  # noqa: E402
import full_universe as fu  # noqa: E402


@pytest.fixture
def fixture_plan(tmp_path, monkeypatch):
    """Synthetic registry driving the loader with floors patched via
    constants (G4 'floors patched via constants')."""
    monkeypatch.setattr(fu, "REGISTRY_MIN", 50)
    monkeypatch.setattr(fu, "MIN_REGIONS", 5)
    path = fx.write_fixture_registry(tmp_path / "universe_cache.json")
    return fu.load_plan(path)


# ── UniversePlan contract (§4) ─────────────────────────────────────────────
def test_plan_byte_exact(fixture_plan):
    assert list(fixture_plan.order) == fx.FIXTURE_ORDER
    assert {r: len(v) for r, v in fixture_plan.regions.items()} == fx.FIXTURE_SIZES
    assert fixture_plan.m_unique == fx.FIXTURE_M_UNIQUE
    assert fixture_plan.registry_ts == fx.FIXTURE_TS
    assert "china" not in fixture_plan.regions  # empty bucket -> no tab


def test_okontrollerat_never_tab_never_counted(fixture_plan):
    # C2-F6.1: international-exclusive members -> dropped list only
    assert sorted(fixture_plan.dropped) == fx.INTL_ONLY
    assert fu.OKONTROLLERAT_KEY not in fixture_plan.order
    assert fu.OKONTROLLERAT_KEY not in fixture_plan.regions
    assert not set(fx.INTL_ONLY) & set(
        t for v in fixture_plan.regions.values() for t in v)
    # and they are NOT inside m_unique:
    assert fx.FIXTURE_M_UNIQUE == sum(len(v) for v in fixture_plan.regions.values())


def test_labels_from_region_meta(fixture_plan):
    assert fixture_plan.label("hongkong") == "Hongkong"
    assert fixture_plan.label("sweden") == "Sverige"


def test_priority_assignment_and_future_key():
    buckets = {"sweden": ["DUP", "S1"], "usa": ["DUP", "U1"],
               "atlantica": ["AT1"], "international": ["U1", "NEW"]}
    assigned = fu.assign_primary_regions(buckets)
    assert assigned["usa"] == ["DUP", "U1"]        # higher priority wins
    assert assigned["sweden"] == ["S1"]             # deduped
    assert "international" not in assigned
    assert assigned["atlantica"] == ["AT1"]
    order = fu.tab_order(assigned)
    assert order[0] == "usa"                        # face first
    assert order[-1] == "atlantica"                 # future key lands last
    assert fu.region_label("atlantica") == "atlantica"  # label -> the key


def test_international_exclusive_is_dropped_not_lost():
    buckets = {"usa": ["A"], "international": ["A", "Z"]}
    assigned = fu.assign_primary_regions(buckets)
    assert assigned[fu.OKONTROLLERAT_KEY] == ["Z"]
    assert fu.m_unique_of(assigned) == 1


# ── S0 floors abort BEFORE any network (§9 G2) ────────────────────────────
def _no_network_source():
    src = (Path(fu.__file__).read_text(encoding="utf-8"))
    for banned in ("yfinance", "requests", "urllib", "http", "socket",
                   "universe_builder", "demo_universe", "_fetch_batch"):
        assert not re.search(rf"^\s*(import|from)\s+\S*{banned}", src, re.M), banned


def test_loader_has_no_network_surface():
    _no_network_source()


def test_floor_red_version_one(tmp_path):
    doc = fx.fixture_doc() | {"version": 1}
    p = tmp_path / "uc.json"
    import json
    p.write_text(json.dumps(doc))
    with pytest.raises(fu.RegistryError, match="version"):
        fu.load_plan(p)


def test_floor_red_500_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(fu, "MIN_REGIONS", 5)
    p = fx.write_fixture_registry(tmp_path / "uc.json")   # 222 unique
    with pytest.raises(fu.RegistryError, match="REGISTRY_MIN"):
        fu.load_plan(p)          # DEFAULT REGISTRY_MIN 10_000 -> red
    with pytest.raises(fu.RegistryError, match="m_unique 222 < REGISTRY_MIN 500"):
        monkeypatch.setattr(fu, "REGISTRY_MIN", 500)
        fu.load_plan(p)


def test_floor_red_few_regions(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(fu, "REGISTRY_MIN", 50)  # isolate the region floor
    doc = fx.fixture_doc()
    doc["tickers"] = {k: v for k, v in doc["tickers"].items()
                      if k in ("usa", "sweden", "germany", "india",
                               "hongkong", "international")}
    p = tmp_path / "uc.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(fu.RegistryError, match="non-empty regions"):
        fu.load_plan(p)          # default MIN_REGIONS = 8


def test_anchor_shrink_red_and_bootstrap_green(fixture_plan, tmp_path, monkeypatch):
    # C2-F5: forge an anchor far above m_unique -> S0 names it
    with pytest.raises(fu.RegistryError, match="shrink"):
        fu.load_plan(tmp_path / "universe_cache.json", anchor_m_unique=1000)
    # C3-F3c: anchor absent (None) -> shrink check passes, first run green
    monkeypatch.setattr(fu, "REGISTRY_MIN", 50)
    monkeypatch.setattr(fu, "MIN_REGIONS", 5)
    assert fu.load_plan(tmp_path / "universe_cache.json").m_unique == 222


def test_dirty_tracked_cache_aborts(tmp_path, monkeypatch):
    """G2 dirty red: a throwaway git checkout with a modified tracked cache."""
    import subprocess
    repo = tmp_path / "repo"
    tracked = repo / "source" / "rocket" / "data"
    tracked.mkdir(parents=True)
    p = fx.write_fixture_registry(tracked / "universe_cache.json")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    p.write_text(p.read_text().replace("USA001", "USA999"))
    monkeypatch.setattr(fu, "REPO_ROOT", repo)
    monkeypatch.setattr(fu, "REGISTRY_MIN", 50)
    monkeypatch.setattr(fu, "MIN_REGIONS", 5)
    with pytest.raises(fu.RegistryError, match="dirty"):
        fu.load_plan(p)


# ── seam C4 + constants home ───────────────────────────────────────────────
def test_cache_filename_extended_escape():
    assert fu.cache_filename("BRK/A") == "BRK_A.csv"
    assert fu.cache_filename("SAAB-B.ST") == "SAAB_B_ST.csv"
    assert fu.cache_filename("BF/B") == "BF_B.csv"
    stem = fu.cache_filename("WSO/B").removesuffix(".csv")
    assert "/" not in stem and "." not in stem and "-" not in stem


def test_real_registry_plan_matches_design_numbers():
    """§2 digit pins on the REAL tracked cache (read-only, no network)."""
    plan = fu.load_plan()
    assert plan.m_unique == 12793
    assert list(plan.order) == ["usa", "sweden", "germany", "india",
                                "hongkong", "japan", "uk", "norway",
                                "finland", "australia", "denmark", "canada",
                                "korea", "switzerland", "france"]
    assert plan.dropped == []          # 0 exclusive international members
    assert {r: len(v) for r, v in plan.regions.items()}["usa"] == 6652
    stems = [fu.cache_filename(t) for r in plan.order for t in plan.regions[r]]
    assert len(set(stems)) == len(stems)
    assert all("/" not in s and "." not in s
               for s in (x.removesuffix(".csv") for x in stems))


def test_m_page_max_one_governing_number():
    assert fu.M_PAGE_MAX == (fu.MAX_HTML_BYTES - fu.CHROME_EST) \
        // fu.BYTES_PER_ROW_EST - fu.ALLA_CAP == 16_113


# ── C3-F1 batching: stride green beside contiguous planted-bad ─────────────
def test_batches_are_deterministic_partition(fixture_plan):
    items = fx.flat_plan_order(fixture_plan)
    b1 = fu.make_batches(items)
    b2 = fu.make_batches(items)
    assert b1 == b2                                   # identical input twice
    flat = [t for b in b1 for t in b]
    assert sorted(flat) == sorted(items)              # every ticker, once
    assert all(0 < len(b) <= fx.BATCH for b in b1)


def test_no_batch_is_all_junk_stride(fixture_plan):
    """GREEN (production batcher): NO 50-batch is 100 % numeric-only even
    with the fixture's 59-run junk block — at EVERY alignment shift."""
    items = fx.flat_plan_order(fixture_plan)
    assert any(fx.is_junk(t) for t in items)
    for shift in range(0, 60):
        shifted = items[shift:] + items[:shift]
        for batch in fu.make_batches(shifted, fx.BATCH):
            assert not all(fx.is_junk(t) for t in batch), \
                f"pure-junk batch at shift {shift}: {batch[:3]}"


def test_planted_bad_contiguous_order_makes_pure_junk_batch(fixture_plan):
    """RED DEMONSTRATION (planted-bad, DA-verdict-c3 C3-F1): the contiguous
    order the design FORBIDS deterministically builds a 100 %-junk 50-batch
    from the same fixture — exactly the live-probed `1974`…`2023` night-1
    brick. This test asserts the BUG EXISTS in the retired order so the
    green stride test can never be vacuous."""
    items = fx.flat_plan_order(fixture_plan)
    contiguous = fx.contiguous_batches(items, fx.BATCH)
    pure = [b for b in contiguous if all(fx.is_junk(t) for t in b)]
    assert pure, "planted-bad setup broken: contiguous order must go pure-junk"
    assert len(pure[0]) == fx.BATCH                   # a FULL 50-ticker dead batch


def test_total_yahoo_death_still_fails_closed(fixture_plan):
    """All-{} is ALL not_fetched even with stride batching — §9 #3 stays
    red under total death (the gate's purpose is intact)."""
    import store_io
    stub = fx.fetch_stub(all_dead=True)
    completed: set[str] = set()
    assignments: dict[str, str] = {}
    for batch in fu.make_batches(fx.flat_plan_order(fixture_plan)):
        got = stub(batch, "1y")
        completed |= set(got)
        for t in batch:
            assignments[t] = store_io.classify_accounting(
                produced_row=False, batch_completed=bool(got),
                has_usable_store=False)
    assert set(assignments) and set(completed) == set()
    reasons = store_io.accounting_reasons(assignments, fixture_plan.m_unique)
    assert reasons and f"not_fetched={len(assignments)}" in reasons[0]


def test_junk_dies_in_completed_batches_not_not_fetched(fixture_plan):
    """§5 step-2's claim made true: with stride batching the 59-run junk
    completes inside mixed batches -> dead_at_fetch; not_fetched stays 0.
    The contiguous order fails the same scenario (red kept visible)."""
    import store_io
    stub = fx.fetch_stub()
    items = fx.flat_plan_order(fixture_plan)
    assignments = {}
    for batch in fu.make_batches(items):
        got = stub(batch, "1y")
        assert got, "stride batching left a deterministically dead batch"
        for t in batch:
            assignments[t] = store_io.classify_accounting(
                produced_row=t in got, batch_completed=bool(got),
                has_usable_store=False)
    assert store_io.accounting_reasons(assignments, fixture_plan.m_unique) == []
    n_junk = sum(1 for t, c in assignments.items()
                 if c == store_io.DEAD_AT_FETCH)
    assert n_junk == fx.JUNK_RUN
    # same fixture, CONTIGUOUS order: the pure batch => 50 not_fetched (red)
    contig_assign = {}
    for batch in fx.contiguous_batches(items):
        got = stub(batch, "1y")
        for t in batch:
            contig_assign[t] = store_io.classify_accounting(
                produced_row=t in got, batch_completed=bool(got),
                has_usable_store=False)
    nf = sum(1 for c in contig_assign.values() if c == store_io.NOT_FETCHED)
    assert nf == fx.BATCH


# ── T13 F3/F4 (MC 10264): production batch shape + bounded digit-junk ───────

def test_make_batches_production_shape_12793_members():
    """ARCH T10 F-1 / DA T9 F3: at the REAL registry size the walk must emit
    ceil(n/size) batches of <= size members — 256 x <=50 (bulk_fetcher.
    BATCH_SIZE, ARCHITECTURE.md:43, the measured 2.1-3.5 s per-batch basis) —
    NEVER the old size-buckets-of-ceil(n/size) shape (50 x 256: one dead call
    = 256 not_fetched, measured timeout math void)."""
    items = [f"TT{i:05d}" for i in range(12_793)]
    batches = fu.make_batches(items)
    assert len(batches) == -(-12_793 // fx.BATCH) == 256
    assert all(0 < len(b) <= fx.BATCH for b in batches)
    assert sorted(t for b in batches for t in b) == items   # exact partition
    assert fu.make_batches(items) == batches                # deterministic


def test_digit_junk_residue_block_spread_max_one_per_batch():
    """DA T9 F4: the residue-aligned numeric block that STACKED a 100 %-junk
    batch under the old size-stride (indices 5,55,...,455 -> batch items
    [5::50]) is now bounded — junk <= batch count means <= ONE digit-only
    member per batch, whatever the alignment."""
    junk_idx = set(range(5, 500, 50))         # 10 members, ALL on one stride
    items = [str(1900 + i) if i in junk_idx else f"TT{i:04d}"
             for i in range(500)]
    # stride the OLD way (items[j::50]) puts every junk member in one batch;
    # assert the multiset first (order-free), then the bound.
    batches = fu.make_batches(items)
    assert sorted(t for b in batches for t in b) == sorted(items)
    assert len(batches) == 10                 # junk count == batch count
    for k, b in enumerate(batches):
        assert sum(1 for t in b if fx.is_junk(t)) <= 1, f"batch {k} stacks junk"
        assert not all(fx.is_junk(t) for t in b)           # never all-dead-by-name


def test_make_batches_500_live_deterministic_small_shape():
    """Shape law pinned at a second size: n == ceil(len/size), sizes <= size,
    empty input -> [] (and never an empty batch)."""
    items = [f"TT{i:04d}" for i in range(500)]
    batches = fu.make_batches(items)
    assert len(batches) == 10 and all(len(b) == 50 for b in batches)
    assert fu.make_batches([]) == []
    assert all(b for b in fu.make_batches(items, 7))       # size not dividing
    assert sorted(t for b in fu.make_batches(items, 7) for t in b) == items
