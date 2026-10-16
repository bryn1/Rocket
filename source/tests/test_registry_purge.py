"""registry_purge (MC 10738): the criterion, its ONE exclusion, idempotence,
derivability of the committed registry, and the REGROWTH tripwires.

Hermetic by default: the criterion runs against a tmp store built by the
test. The two checks that read the REAL store (byte-identical regeneration,
walked-fileless == the named exclusion) skip when no store is present — the
suite is proven green store-present AND store-absent (MC 10309 hermeticity).

Every guard here has a planted-fault twin that proves the green one is not
vacuous (fleet rule: a check that has never failed is not a check).
"""
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
sys.path.insert(0, str(SOURCE / "scripts"))
sys.path.insert(0, str(TESTS))
import fu_fixtures as fx                                # noqa: E402
import full_universe as fu                              # noqa: E402
import registry_purge as rp                             # noqa: E402

STORE_PRESENT = fu.CACHE_DIR.is_dir()
needs_store = pytest.mark.skipif(not STORE_PRESENT,
                                 reason="no bar store in this checkout")

# The ONE exclusion, as of this commit. T36 probed the dash form live (bars);
# MC 10761 landed the request-form fix on main (9cda546), so their bars are
# expected from the 2026-10-16 nightly — INFERRED, not yet in the store, which
# is exactly why the purge must not delete them today.
CLASS_SHARE_12 = {"BRK/A", "BRK/B", "BF/A", "BF/B", "HEI/A", "HVT/A", "AKO/A",
                  "AKO/B", "BIO/B", "CRD/A", "CRD/B", "WSO/B"}
# The japan contamination that regrowth must never restore (T34 §1, P0-2):
# 811 contiguous 4-digit codes 1000.T-1810.T. MAX_RUN leaves room for the
# legitimate 20-run that survives the purge (1472.T-1491.T, 20 members).
MAX_CONTIGUOUS_NUMERIC_RUN = 25


def _doc(buckets, ts="2026-10-08T20:17:28.207667+00:00"):
    return {"tickers": buckets, "timestamp": ts,
            "source": "fixture", "version": 2}


def _store(tmp_path, members):
    d = tmp_path / "raw"
    d.mkdir(parents=True, exist_ok=True)
    for t in members:
        (d / fu.cache_filename(t)).write_text("date,open,high,low,close,volume\n",
                                              encoding="utf-8")
    return d


def _committed_doc():
    return json.loads(fu.REGISTRY_PATH.read_text(encoding="utf-8"))


MANIFEST_PATH = rp.REPO_ROOT / ".audits/20261015-t35-registry-purge" \
    / "removal_manifest.tsv"


def _manifest_rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == rp.MANIFEST_HEADER
    out = []
    for ln in lines[1:]:
        region, index, member, reason = ln.split("\t")
        out.append({"region": region, "index": int(index), "member": member,
                    "reason": reason})
    return out


def _pre_purge_state():
    """Reconstruct the pre-purge registry from the committed cache + the
    committed manifest (which is why the manifest carries region AND index):
    re-insert each removed member at its recorded index, ascending. Returns
    (registry text, rows)."""
    rows = _manifest_rows(MANIFEST_PATH)
    doc = _committed_doc()
    buckets = {k: list(v) for k, v in doc["tickers"].items()}
    for row in sorted(rows, key=lambda r: (r["region"], r["index"])):
        buckets[row["region"]].insert(row["index"], row["member"])
    return rp.serialise({**doc, "tickers": buckets}), rows


# ── the criterion + its ONE exclusion ──────────────────────────────────────
def test_purge_removes_fileless_keeps_fileful(tmp_path):
    buckets = {"usa": ["KEEP1", "GONE1", "KEEP2"], "uk": ["GONE1", "KEEP3"],
               "international": ["GONE2", "KEEP1"]}
    store = _store(tmp_path, ["KEEP1", "KEEP2", "KEEP3"])
    doc, removals, keep = rp.transform(_doc(buckets), store, us_directory=set())
    assert doc["tickers"] == {"usa": ["KEEP1", "KEEP2"], "uk": ["KEEP3"],
                              "international": ["KEEP1"]}
    assert {r["member"] for r in removals} == {"GONE1", "GONE2"}
    # GONE1 sat in two buckets -> two membership deletions (§9 #3's unit)
    assert len(removals) == 3
    assert [(r["region"], r["index"]) for r in removals if
            r["member"] == "GONE1"] == [("usa", 1), ("uk", 0)]
    assert keep == frozenset()


def test_class_share_exclusion_is_derived_not_hand_listed(tmp_path):
    """Only the `/` members the in-repo listing directory carries survive."""
    buckets = {"usa": ["BRK/A", "FAKE/X", "ZED", "NOFILE-PLAIN"]}
    store = _store(tmp_path, ["ZED"])                    # all others fileless
    us_dir = {"BRK/A", "OTHER/LISTED"}                   # FAKE/X NOT listed
    doc, _, keep = rp.transform(_doc(buckets), store, us_directory=us_dir)
    assert keep == frozenset({"BRK/A"})
    assert doc["tickers"]["usa"] == ["BRK/A", "ZED"]     # unlisted junk goes
    assert rp.fileless_members(buckets, store) == ["BRK/A", "FAKE/X",
                                                    "NOFILE-PLAIN"]


def test_real_us_directory_yields_exactly_the_named_twelve():
    """The directory-derived rule still selects the twelve T36 probed — if the
    directory changes, this pin goes red before the purge can over-keep."""
    members = {t for v in _committed_doc()["tickers"].values() for t in v}
    assert rp.class_share_exclusions(members, rp.load_us_directory()) \
        == frozenset(CLASS_SHARE_12)


# ── determinism: byte-identical regeneration, idempotence, fail-closed ─────
def test_transform_is_idempotent_on_a_fixture_store(tmp_path):
    buckets = {"usa": ["A1", "B2", "C3"], "uk": ["D4"]}
    store = _store(tmp_path, ["A1", "C3", "D4"])
    doc1, rem1, _ = rp.transform(_doc(buckets), store, us_directory=set())
    doc2, rem2, _ = rp.transform(doc1, store, us_directory=set())
    assert rp.serialise(doc2) == rp.serialise(doc1)      # second pass: no-op
    assert rem2 == []
    assert rem1 and {r["member"] for r in rem1} == {"B2"}


def test_committed_cache_is_derivable_and_the_purge_is_idempotent(tmp_path):
    """DoD 5, both halves. (a) DERIVABLE: rebuild the pre-purge registry from
    the committed manifest + committed cache, run the committed script over it,
    and the bytes that come out ARE the committed cache — provenance, not an
    edit. (b) IDEMPOTENT: run it again over its own output and the file and the
    manifest are unchanged."""
    if not (STORE_PRESENT and MANIFEST_PATH.exists()):
        pytest.skip("needs the bar store and the committed removal manifest")
    pre_text, rows = _pre_purge_state()
    pre, once, twice = tmp_path / "pre.json", tmp_path / "post1.json", \
        tmp_path / "post2.json"
    man1, man2 = tmp_path / "m1.tsv", tmp_path / "m2.tsv"
    pre.write_text(pre_text, encoding="utf-8")
    assert len(rows) == 3667
    assert rp.run(pre, fu.CACHE_DIR, out=once, manifest=man1) == 0
    assert once.read_text(encoding="utf-8") == \
        fu.REGISTRY_PATH.read_text(encoding="utf-8")     # byte-identical
    assert man1.read_text(encoding="utf-8") == MANIFEST_PATH.read_text(
        encoding="utf-8")                                # manifest reproduced
    assert rp.run(once, fu.CACHE_DIR, out=twice, manifest=man2) == 0
    assert twice.read_text(encoding="utf-8") == once.read_text(
        encoding="utf-8")                                # nothing left to purge
    assert man2.read_text(encoding="utf-8") == rp.MANIFEST_HEADER + "\n"


def test_run_refuses_a_registry_it_could_reformat(tmp_path):
    """Planted fault for the fail-closed serialisation guard: a registry in
    another shape must exit 1 and write NOTHING (no 15,000-line reformat)."""
    doc = _doc({"usa": ["A1"], "uk": ["B2"]})
    bad = tmp_path / "universe_cache.json"
    bad.write_text(json.dumps(doc, indent=4), encoding="utf-8")   # planted
    out, man = tmp_path / "out.json", tmp_path / "m.tsv"
    assert rp.run(bad, _store(tmp_path, ["A1"]), out=out, manifest=man) == 1
    assert not out.exists() and not man.exists()


def test_manifest_names_nothing_that_survived_and_keeps_the_twelve():
    """The removal record is consistent with the committed cache: every row is
    gone, and none of the twelve class-share members is on it."""
    if not MANIFEST_PATH.exists():
        pytest.skip("manifest lives in the audit run dir")
    rows = _manifest_rows(MANIFEST_PATH)
    survivors = {t for v in _committed_doc()["tickers"].values() for t in v}
    removed = {r["member"] for r in rows}
    assert not (removed & survivors)                      # all really removed
    assert not (removed & CLASS_SHARE_12)                 # the 12 were NOT
    # every purged member really has no bar file (criterion, live store)
    if STORE_PRESENT:
        assert all(not (fu.CACHE_DIR / fu.cache_filename(t)).exists()
                   for t in removed)


# ── REGROWTH tripwires (DoD 3) ─────────────────────────────────────────────
def contiguous_numeric_runs(buckets):
    """{(region, suffix): longest run of consecutive integer code roots}.
    The TSE sweep signature: 1000.T, 1001.T, 1002.T ... is one 811-run."""
    out = {}
    for region, members in buckets.items():
        groups = {}
        for t in members:
            root, _, suffix = t.partition(".")
            if not root.isdigit():
                continue
            groups.setdefault(suffix, []).append(int(root))
        for suffix, vals in groups.items():
            vals.sort()
            best = cur = 1
            for a, b in zip(vals, vals[1:]):
                cur = cur + 1 if b == a + 1 else 1
                best = max(best, cur)
            out[(region, suffix)] = best
    return out


def phantom_shaped_regions(buckets, limit=MAX_CONTIGUOUS_NUMERIC_RUN):
    return {k: v for k, v in contiguous_numeric_runs(buckets).items()
            if v > limit}


def test_committed_registry_has_no_phantom_sweep():
    """GREEN guard: no bucket carries a contiguous numeric block — the class
    the T34 census found (japan 811 codes 1000.T-1810.T, 633 fileless)."""
    runs = contiguous_numeric_runs(_committed_doc()["tickers"])
    assert phantom_shaped_regions(_committed_doc()["tickers"]) == {}
    assert runs.get(("japan", "T"), 0) <= MAX_CONTIGUOUS_NUMERIC_RUN


def test_planted_bad_phantom_sweep_goes_red(tmp_path):
    """PLANTED FAULT (the sweep the guard exists to catch): restoring the
    1000.T-1199.T block must light the tripwire up."""
    buckets = {"japan": [f"{1000 + i}.T" for i in range(200)],
               "usa": ["90001", "90002", "90003"]}
    assert phantom_shaped_regions(buckets) == {("japan", "T"): 200}
    assert contiguous_numeric_runs(buckets)[("usa", "")] == 3   # under limit


def test_planted_bad_fileless_reinjection_goes_red(tmp_path):
    """PLANTED FAULT for the walked-fileless tripwire: re-injecting two
    members with no bars (the unlisted-phantom class) is a violation; the
    twelve directory-listed class-share members are the ONLY tolerated ones."""
    def violation_members(buckets, store, us_dir):
        members = {t for v in buckets.values() for t in v}
        tolerated = rp.class_share_exclusions(members, us_dir)
        return set(rp.fileless_members(buckets, store, tolerated))

    buckets = {"usa": ["BRK/A", "ZZZPHANTOM1", "ZZZPHANTOM2", "GOOD1"]}
    store = _store(tmp_path, ["GOOD1"])
    assert violation_members(buckets, store, {"BRK/A"}) == \
        {"ZZZPHANTOM1", "ZZZPHANTOM2"}
    assert violation_members({"usa": ["BRK/A", "GOOD1"]}, store, {"BRK/A"}) \
        == set()                             # the tolerated twelve alone: clean


@needs_store
def test_committed_registry_walked_fileless_is_within_the_twelve():
    """GREEN guard, live store: after the purge the ONLY walked members
    without bars may be the twelve kept by the exclusion — a SUBSET test, not
    an equality: MC 10761's class-share form fix (9cda546) should land their
    bars from tonight's nightly, and then an EMPTY set is correct too. Any
    fileless member OUTSIDE the tolerated twelve is a re-injection and reds."""
    doc = _committed_doc()
    assigned = fu.assign_primary_regions(doc["tickers"])
    walked = {t for k, v in assigned.items()
              if k != fu.OKONTROLLERAT_KEY for t in v}
    fileless = {t for t in walked if not (fu.CACHE_DIR /
                                          fu.cache_filename(t)).exists()}
    assert fileless <= CLASS_SHARE_12


@needs_store
def test_committed_registry_still_clears_the_s0_floors():
    """The purge is allowed to shrink the universe, not to break it: the S0
    loader floors still hold on the committed cache (m_unique over
    REGISTRY_MIN, MIN_REGIONS tabs) and the partition stays CLOSED — every
    unique member lands in exactly one tab or in `okontrollerat`, so nothing
    was orphaned or duplicated on the way through the transform."""
    plan = fu.load_plan()                                # git_check default
    assert plan.m_unique >= fu.REGISTRY_MIN
    assert len(plan.order) >= fu.MIN_REGIONS
    assert sum(len(v) for v in plan.regions.values()) == plan.m_unique
    assert plan.m_unique + len(plan.dropped) == len(
        {t for v in _committed_doc()["tickers"].values() for t in v})
