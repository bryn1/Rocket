"""MC 10718: the in-repo TSE/KRX listing source and its code-shape gate.

The gate lives at the LISTING PARSER, not at apply_region_suffix (MC 10342
rule 1 keeps an already-suffixed member VERBATIM, so the registry's known
contaminant `0005930.KS` — 7-digit root next to the landed 005930_KS.csv,
MC 10689 — sails through the suffix step untouched). Fixtures mirror the
landed roster files byte-format ('#' provenance + code,segment,name);
suffix expectations are READ FROM REGION_META at test time, never pinned.
"""
import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))

import rocket.data.universe_builder as ub                        # noqa: E402
import rocket.data.universe_suffix as us                         # noqa: E402
import rocket.data.universe_tse_krx as lk                        # noqa: E402
from rocket.data.universe_regions import REGION_META             # noqa: E402

# ── fixtures: mini rosters in the landed files' exact format ──────────────
TSE_FX = """\
# provenance
code,segment,name
1301,Prime Market (Domestic),KYOKUYO
7203,Prime Market (Domestic),TOYOTA MOTOR
7003,Prime Market (Domestic),HITACHI
9101,Standard Market(Domestic),NYK LINE
12345,Prime Market (Domestic),FIVE-DIGIT FRAUD
130A,Prime Market (Domestic),JPX ETN FORM
700.HK,Prime Market (Domestic),HKEX NON-MEMBER
"""
KRX_FX = """\
# provenance
code,segment,name
005930,KOSPI,SAMSUNG ELECTRONICS
000660,KOSPI,SK HYNIX
179880,KOSDAQ,MELCON
0005930,KOSPI,SEVEN-DIGIT FRAUD
0005930.KS,KOSPI,SUFFIXED SEVEN-DIGIT FRAUD
0203K0,KOSPI,LETTER-CODE PREFERRED
700.HK,KOSPI,HKEX NON-MEMBER
"""


@pytest.fixture
def rosters(tmp_path):
    tse, krx = tmp_path / "tse.csv", tmp_path / "krx.csv"
    tse.write_text(TSE_FX, encoding="utf-8")
    krx.write_text(KRX_FX, encoding="utf-8")
    return tse, krx


def _built(rosters):
    return lk.build_listing_regions(tse_path=rosters[0],
                                    krx_path=rosters[1])


# ── the code-shape rule itself ─────────────────────────────────────────────
@pytest.mark.parametrize("member", ["7003", "7003.T"])
def test_japan_rule_accepts_well_shaped(member):
    assert lk.region_member_ok("japan", member)


@pytest.mark.parametrize("member",
                         ["12345",        # 5-digit TSE form (DoD)
                          "700", "123456", "130A",
                          "005930.KS", "700.HK"])
def test_japan_rule_rejects_malformed_and_foreign(member):
    assert not lk.region_member_ok("japan", member)


@pytest.mark.parametrize("member", ["005930", "005930.KS", "179880.KQ"])
def test_korea_rule_accepts_well_shaped(member):
    assert lk.region_member_ok("korea", member)


@pytest.mark.parametrize("member",
                         ["0005930",      # 7-digit bare
                          "0005930.KS",    # the MC 10689 registry contaminant
                          "0203K0",        # KRX letter-code preferred form
                          "700.HK", "005930.T", "1234"])
def test_korea_rule_rejects_malformed_and_foreign(member):
    assert not lk.region_member_ok("korea", member)


def test_foreign_hk_non_member_admitted_nowhere():
    """`700.HK` is a member of neither japan nor korea (HKEX code, 3-digit
    root + foreign tail): rejected for BOTH regions."""
    assert not lk.region_member_ok("japan", "700.HK")
    assert not lk.region_member_ok("korea", "700.HK")


# ── the parser over fixture rosters ───────────────────────────────────────
def test_parser_admits_only_the_well_shaped_selected_members(rosters):
    out = _built(rosters)
    jp = REGION_META["japan"]["suffixes"][0]
    ks = REGION_META["korea"]["suffixes"][0]
    assert out["japan"] == ["1301" + jp, "7003" + jp, "7203" + jp]
    assert out["korea"] == ["000660" + ks, "005930" + ks]


def test_parser_rejects_the_named_contaminants(rosters):
    out = _built(rosters)
    # 5-digit TSE codes, 7-digit suffixed KRX, letter codes and the HKEX
    # non-member never surface — three named examples each side:
    assert not any("12345" in m for m in out["japan"])
    assert not any("130A" in m for m in out["japan"])
    assert not any(m.endswith(".HK") for m in out["japan"])
    assert "0005930.KS" not in out["korea"]      # exactly 005930.KS survives
    assert "0203K0" not in [m[:6] for m in out["korea"]]
    assert not any(m.endswith(".HK") for m in out["korea"])


def test_segment_selection_is_not_the_shape_rule(rosters):
    """9101 (Standard) and 179880 (KOSDAQ) are well-shaped but NOT selected
    — selection drops them; the shape rule does not reject them (KOSDAQ
    still passes the rule when its segment is selected below)."""
    out = _built(rosters)
    assert not any(m.startswith("9101") for m in out["japan"])
    assert not any(m.startswith("179880") for m in out["korea"])


def test_krx_second_suffix_is_region_meta_kosdaq_not_a_reinvention(rosters,
                                                                   monkeypatch):
    monkeypatch.setattr(lk, "KRX_SELECTED_MARKETS", ("KOSPI", "KOSDAQ"))
    out = _built(rosters)
    kq = REGION_META["korea"]["suffixes"][1]
    assert "179880" + kq in out["korea"]
    # the suffix step already knows .KQ: it passes it, does NOT re-suffix
    assert us.apply_region_suffix("korea", ["179880" + kq]) == ["179880" + kq]


def test_selected_members_pass_the_suffix_choke_point_verbatim(rosters):
    """apply_region_suffix (MC 10342, THE choke point) is a fixed point on
    the parser's output — nothing here authors a second suffix mechanism."""
    out = _built(rosters)
    assert us.apply_region_suffix("japan", out["japan"]) == out["japan"]
    assert us.apply_region_suffix("korea", out["korea"]) == out["korea"]


# ── the LANDED in-repo rosters (host-reachability, budget) ────────────────
def test_landed_rosters_build_on_this_host_within_budget():
    """The two exchange rosters ship IN-REPO: no /srv root, no reconcile_m3,
    no network — this call is the host-reachability proof the registry
    rebuild (MC 10710) waits on. Size is bounded by the page headroom."""
    out = lk.build_listing_regions()
    jp, kr = out["japan"], out["korea"]
    assert jp and kr
    assert all(re.fullmatch(r"\d{4}\.T", m) for m in jp)
    assert all(re.fullmatch(r"\d{6}\.KS", m) for m in kr)
    assert len(jp) + len(kr) <= lk.LISTING_BUDGET_TOTAL
    # japan is a NAMED region of the refresh guard: never below 0.8x997:
    assert len(jp) >= 800
    # the LANDED rosters genuinely carry the malformed class (the rule
    # bites real exchange data, it is not fixture luck):
    bad_tse = [c for c, *_ in lk._read_roster(lk.TSE_FILE)
               if not re.fullmatch(r"\d{4}", c)]
    assert ["130A", "131A", "132A"] <= bad_tse[:4]
    bad_krx = [c for c, *_ in lk._read_roster(lk.KRX_FILE)
               if not re.fullmatch(r"\d{6}", c)]
    assert "0220W0" in bad_krx


def test_budget_guard_refuses_an_oversized_selection(monkeypatch):
    monkeypatch.setattr(lk, "LISTING_BUDGET_TOTAL", 10)
    with pytest.raises(ValueError, match="page headroom budget"):
        lk.build_listing_regions()


# ── the builder seam, live on this host ───────────────────────────────────
def test_builder_generated_lists_are_host_reachable():
    """_load_generated_region_lists used to die on `from reconcile_m3 import`
    (dead /srv root). It now returns in-repo-shaped japan/korea buckets on
    THIS host and the MC 10689 contaminant classes cannot re-enter."""
    gen = ub._load_generated_region_lists()
    assert gen["japan"] and gen["korea"]
    assert all(re.fullmatch(r"\d{4}\.T", m) for m in gen["japan"])
    assert all(re.fullmatch(r"\d{6}\.(KS|KQ)", m) for m in gen["korea"])
    assert "1001.T" not in gen["japan"]          # the sweep 1000.T..1810.T
    assert "0005930.KS" not in gen["korea"]      # the malformed 7-digit
    assert gen["japan"] == lk.build_listing_regions()["japan"]


# ── planted-bad RED proof: the green tests assert on the RULE ─────────────
def test_planted_bad_permissive_rule_admits_the_contaminants(rosters,
                                                             monkeypatch):
    """With the code-shape rule planted permissive, the SAME fixtures admit
    12345 into japan and the exact registry contaminant 0005930.KS back into
    korea — proving the green tests above bite on the rule, not on fixture
    luck. (Second RED proof: an external mutation of the committed module,
    transcript on the card's RESULT.md.)"""
    monkeypatch.setitem(lk.SHAPES, "japan", re.compile(r"\w+"))
    monkeypatch.setitem(lk.SHAPES, "korea", re.compile(r"\w+"))
    out = _built(rosters)
    assert "12345.T" in out["japan"]
    assert "0005930.KS" in out["korea"]
