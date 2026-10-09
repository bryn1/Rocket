"""MC 10342: builder suffix-hygiene regression (kill the silent-revert).

T25 (761e26e) hygiened the TRACKED registry data; this pins the CODE that
regenerates it: a builder run over OFFLINE fixtures (the same seams
test_refresh_universe._stub_builder_seams established — module functions
monkeypatched, zero network, no dates involved: force builds never touch
timestamps) must emit every non-US bucket REGION_META-primary-suffixed, so
any future suffix regression goes RED. Suffix expectations are READ FROM
REGION_META at test time — never hardcoded here.

Fixture inputs mirror the real builder inputs: bare TIDM/ASX codes (what the
LSE/ASX wiki tables carry), TSX bare roots + class/unit dot-forms (GIB.A,
AP.UN — committed evidence: the pre-T25 international bucket holds 19 *.UN
forms, the T25 canada bucket their *.UN.TO twins), and SIX .SZ/.SN forms
(the embedded Swiss literals verbatim).
"""
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))

import rocket.data.universe_builder as ub                        # noqa: E402
import rocket.data.universe_suffix as us                         # noqa: E402
from rocket.data.universe_regions import REGION_META             # noqa: E402

# ── offline fixtures (>=20 members: below that the builder legitimately
#    skips a wiki region and lands on the embedded fallback — covered too) ──
US_FX = ["AAPL", "MSFT", "BRK.B", "BF.B"]
UK_SCRAPE = ["3I", "AAF", "BARC", "BP", "HSBA", "VOD", "GSK", "AZN", "ULVR",
             "DGE", "SHEL", "RIO", "GLEN", "LLOY", "NWG", "PRU", "STAN",
             "MNDI", "MKSA", "WEIR", "WTB", "MKS", "REL", "RRL"]
AU_SCRAPE = ["BHP", "CBA", "WES", "CCL", "CSL", "NAB", "ANZ", "WBC", "FMG",
             "MQG", "GMG", "COL", "WOW", "TCL", "TLS", "STO", "S32", "QBE",
             "WDS", "REA", "4DX", "A2M", "ALZ", "ALL"]
CA_SCRAPE = ["ABX", "BMO", "CM", "RY", "TD", "SHOP", "ENB", "TRP", "CNQ",
             "SU", "IMO", "MFC", "TRI", "QSR", "K", "BNS", "CNR", "CP",
             "FTS", "WCN", "GIB.A", "AP.UN", "ACO.X", "BBD.B", "CTC.A",
             "MAG", "VCN.TO"]                       # + one .V venture form
CH_SCRAPE = ["NESN.SZ", "NOVN.SZ", "ROG.SZ", "ABBN.SZ", "GIVN.SZ", "ZURN.SZ",
             "SREN.SZ", "LONN.SZ", "UBSG.SZ", "CSGN.SZ", "SIKA.SZ",
             "KNIN.SZ", "SCMN.SZ", "LISN.SZ", "VONN.SZ", "2000.SN",
             "PAXN", "BMOG.SZ", "ANEA.SZ", "ASYL.SZ", "MILL.SZ", "HOLN.SZ",
             "EVTN.SZ", "KSNR.SZ"]                  # embedded-literal forms
MANAGED_FX = {
    "sweden": ["SAAB-B.ST", "ERIC-B.ST", "ATLASCO.ST"],
    "norway": ["EQNR.OL", "DNB.OL"],
    "denmark": ["NOVO-B.CO", "MAERSK-B.CO"],
    "finland": ["NESU.HE"],
    "germany": ["SAP.DE", "BMW.DE"],
    "france": ["AIR.PA", "MC.PA"],
    "japan": ["7203.T"],
    "hongkong": ["0700.HK"],
    "china": ["600519.SS", "000001.SZ"],
    "india": ["RELIANCE.NS", "INFY.BO"],
    "korea": ["005930.KS", "005380.KQ"],
}
_PAGE_FIXTURES = {
    "FTSE_100_Index": UK_SCRAPE,
    "S%26P_ASX_200": AU_SCRAPE,
    "S%26P/TSX_Composite_Index": CA_SCRAPE,
    "SIX_Swiss_Exchange": CH_SCRAPE,
}


@pytest.fixture
def built(monkeypatch):
    """Full force build, in memory, all network/file seams replaced by the
    fixtures above (same seam set test_refresh_universe uses, plus
    _load_generated_region_lists so the M4 path never touches /srv)."""
    monkeypatch.setattr(ub, "_universe_cache", None)
    monkeypatch.setattr(ub, "_load_us_tickers_from_csv", lambda: set(US_FX))
    monkeypatch.setattr(ub, "_load_tickers_from_local_source",
                        lambda source: [])
    monkeypatch.setattr(ub, "_extract_tickers_from_wikipedia",
                        lambda page: list(_PAGE_FIXTURES.get(page, [])))
    monkeypatch.setattr(ub, "_load_generated_region_lists",
                        lambda: {r: list(v) for r, v in MANAGED_FX.items()})
    return ub._build_universe(force_refresh=True, write_cache=False)


def _primary(region):
    return REGION_META[region]["suffixes"][0]


# ── DoD 2: uk/au/ca/swiss buckets — EVERY member region-primary-suffixed ──
@pytest.mark.parametrize("region", ["uk", "australia", "canada", "switzerland"])
def test_bucket_members_all_carry_region_primary_suffix(built, region):
    suf = _primary(region)
    assert built[region], f"{region} bucket empty"
    bare = [m for m in built[region] if not m.endswith(suf)]
    assert not bare, f"{region} members without {suf}: {bare}"
    # the fixture's bare inputs landed in suffixed form, not dropped:
    roots = {"uk": ["3I", "BP"], "australia": ["BHP", "4DX"],
             "canada": ["ABX", "MAG"], "switzerland": ["PAXN", "ROG"]}
    assert {r + suf for r in roots[region]} <= set(built[region])


def test_no_foreign_exchange_suffix_survives_any_bucket(built):
    """The wrong-suffix Swiss form (pre-fix builder emitted .SZ/.SN) can
    never survive for ANY single-market region: a member may only carry a
    suffix the region itself registers in REGION_META. usa/international
    are primary-None (aggregate/pass-through) and exempt by design."""
    all_sufs = {s for meta in REGION_META.values()
                for s in meta["suffixes"] if s}
    for region, bucket in built.items():
        allowed = set(REGION_META[region]["suffixes"]) - {None}
        if not allowed:
            continue
        for m in bucket:
            foreign = [s for s in all_sufs - allowed if m.endswith(s)]
            assert not foreign, f"{region}: {m} carries {foreign}"


def test_managed_buckets_carry_allowed_suffixes(built):
    for region, fx in MANAGED_FX.items():
        for want in fx:
            assert want in built[region], f"{region} lost {want}"


# ── DoD 2b: international aggregate is resolve-shaped, no stale drop-forms ──
def test_international_members_all_resolve_shaped(built):
    all_sufs = {s for meta in REGION_META.values()
                for s in meta["suffixes"] if s}
    stale = [m for m in built["international"]
             if not any(m.endswith(s) for s in all_sufs)]
    assert not stale, f"international carries stale drop-forms: {stale}"
    intl = set(built["international"])
    # the pre-fix forms are GONE (this is the 686-okontrollerat class):
    assert not intl & {"BP", "ABBN.SZ", "GIB.A", "AP.UN", "2000.SN", "BHP"}
    # their suffixed twins are PRESENT:
    assert {"BP.L", "ABBN.SW", "GIB.A.TO", "AP.UN.TO", "2000.SW"} <= intl
    # usa never leaks into the non-US aggregate (pre-existing semantics):
    assert not intl & set(US_FX)


# ── embedded-fallback path (get_universe_count direct caller) ──────────────
def test_embedded_fallback_path_is_suffixed(monkeypatch):
    monkeypatch.setattr(ub, "_load_generated_region_lists",
                        lambda: {r: list(v) for r, v in MANAGED_FX.items()})
    fb = ub._build_embedded_fallback()
    for region in ("uk", "australia", "canada", "switzerland"):
        suf = _primary(region)
        assert fb[region] and all(m.endswith(suf) for m in fb[region])
    # T25 byte-comparison ground truth for the embedded literals:
    assert "GIB.A.TO" in fb["canada"]          # embedded "GIB.A" + .TO (T25)
    assert "NESN.SW" in fb["switzerland"]      # embedded "NESN.SZ" -> .SW
    assert not any(m.endswith((".SZ", ".SN")) for m in fb["switzerland"])


# ── the mapping unit itself (universe_suffix) ───────────────────────────────
def test_tsx_class_and_unit_roots_keep_t25_shape():
    """T25 committed canada bucket forms: GIB.A.TO / AP.UN.TO / ACO.X.TO /
    BBD.B.TO — root kept, primary appended. (Card TSX note: the builder's
    real inputs DO carry *.UN unit forms — 19 in the committed
    international bucket, twins in canada; T25's canonical shape is the
    dotted root + '.TO', which this pins.)"""
    assert us.apply_region_suffix("canada", ["GIB.A", "AP.UN", "ACO.X",
                                             "BBD.B"]) == \
        ["ACO.X.TO", "AP.UN.TO", "BBD.B.TO", "GIB.A.TO"]


def test_region_registered_secondaries_survive():
    """A suffix the region's own REGION_META tuple registers (.V, .SZ, .BO,
    .KQ...) is resolve-shaped and passes through — a different EXCHANGE
    must never be re-suffixed into the primary."""
    assert us.apply_region_suffix("canada", ["VCN.V"]) == ["VCN.V"]
    assert us.apply_region_suffix("china", ["600519.SS", "000001.SZ"]) == \
        ["000001.SZ", "600519.SS"]
    assert us.apply_region_suffix("india", ["INFY.BO"]) == ["INFY.BO"]


def test_usa_and_international_pass_through():
    assert us.apply_region_suffix("usa", ["BRK.B", "AAPL"]) == \
        ["AAPL", "BRK.B"]
    assert us.apply_region_suffix("international", ["Z.L", "A"]) == \
        ["A", "Z.L"]


def test_mapping_is_idempotent_every_region():
    samples = {"uk": UK_SCRAPE, "australia": AU_SCRAPE, "canada": CA_SCRAPE,
               "switzerland": CH_SCRAPE, "usa": US_FX,
               "international": ["A", "B.L"], "china": ["000001.SZ"]}
    for region, syms in samples.items():
        once = us.apply_region_suffix(region, syms)
        assert us.apply_region_suffix(region, once) == once


# ── planted-bad RED proof: without the hygiene the fixtures DO revert ───────
def test_planted_bad_identity_hygiene_reproduces_the_revert(monkeypatch):
    """If the choke point is removed (identity map planted), the SAME
    fixture build emits suffix-less uk/au/ca and .SZ/.SN Swiss forms —
    exactly the T25 revert this card kills. Proves the green tests above
    assert on the hygiene, not on fixture luck."""
    monkeypatch.setattr(ub, "_universe_cache", None)
    monkeypatch.setattr(ub, "_load_us_tickers_from_csv", lambda: set(US_FX))
    monkeypatch.setattr(ub, "_load_tickers_from_local_source",
                        lambda source: [])
    monkeypatch.setattr(ub, "_extract_tickers_from_wikipedia",
                        lambda page: list(_PAGE_FIXTURES.get(page, [])))
    monkeypatch.setattr(ub, "_load_generated_region_lists",
                        lambda: {r: list(v) for r, v in MANAGED_FX.items()})
    monkeypatch.setattr(ub, "apply_region_suffix",
                        lambda region, syms: sorted(set(syms)))
    bad = ub._build_universe(force_refresh=True, write_cache=False)
    assert "BP" in bad["uk"] and "BP.L" not in bad["uk"]
    assert "ABBN.SZ" in bad["switzerland"]
    assert "AP.UN" in bad["international"]      # stale drop-form class back
