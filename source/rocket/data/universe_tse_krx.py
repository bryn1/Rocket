"""MC 10718: the IN-REPO TSE/KRX listing source — rosters + parser + gate.

The universe builder's japan/korea buckets used to be generated from
/srv/workspace/rocket-region-universe/data (reconcile_m3 + asia_raw). That
root died with vm105 (MC 10563): `import reconcile_m3` fails on this host,
so scripts/refresh_universe.py's default builder died before producing
anything and universe_cache.json was INERT. This module is the replacement
source: the two exchanges' own listing rosters, landed IN-REPO as plain CSV
(listings_tse.csv / listings_krx.csv, provenance in their '#' headers), plus
the parser that turns them into Yahoo RESOLVE-SHAPED members.

CODE-SHAPE RULE — enforced HERE, at the listing parser, never downstream:
  japan (TSE): member root exactly 4 digits     (1301   -> 1301.T)
  korea (KRX): member root exactly 6 digits     (005930 -> 005930.KS)
apply_region_suffix (MC 10342) keeps an already-suffixed member VERBATIM, so
the registry's known contaminants — the 7-digit `0005930.KS` next to the
landed 005930_KS.csv, the 5-digit TSE forms of the 1000.T..1810.T sweep
(MC 10689) — sail through that choke point untouched; the ONLY place a
malformed code can be caught is where the roster is parsed. A FOREIGN
dotted tail (e.g. HKEX `700.HK`) is never stripped for the shape check, so
non-members are rejected for BOTH regions. Accepted members re-parse
(strip-own-suffix-then-check): the rule is a fixed point on its own output.

SELECTION — explicit, not completeness: the generated page carries m_unique
12,691 against M_PAGE_MAX 16,113 (full_universe.py), i.e. ~3,400 members of
headroom, while the full shape-valid TSE+KRX re-source is ~6,700 — it will
NOT fit, and every member of the selected list must be probeable for
resolvability (MC 10710). So: japan = TSE Prime Market rows, korea = KRX
KOSPI rows. Every other roster row (Standard/Growth/ETF/REIT/PRO, KOSDAQ,
KONEX) is LANDED and PARSED — counts honest, rule exercised on real data —
but not selected. Suffixes are read from REGION_META at call time
(single source of truth); apply_region_suffix remains THE suffix choke
point — its rule 1 passes these already-shaped members through unchanged.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from . import universe_regions

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent
TSE_FILE = DATA_DIR / "listings_tse.csv"
KRX_FILE = DATA_DIR / "listings_krx.csv"

# ── the code-shape rule (MC 10718): exchange listing facts ─────────────────
TSE_CODE_RE = re.compile(r"\d{4}")
KRX_CODE_RE = re.compile(r"\d{6}")
SHAPES = {"japan": TSE_CODE_RE, "korea": KRX_CODE_RE}

# Page headroom: M_PAGE_MAX 16,113 - committed m_unique 12,691 = 3,422
# (constants home: scripts/full_universe.py; a rocket.data module must not
# import scripts, so the derived figure is pinned here and asserted in
# tests/test_universe_tse_krx.py). A selected list above this cannot ship.
LISTING_BUDGET_TOTAL = 3_400

# Explicit selection (module docstring). TSE section spellings differ
# between files ("Prime Market (Domestic)" / "Prime Market(Foreign)"), so
# segments are PREFIX-matched, never spelled exactly.
TSE_SELECTED_SEGMENTS = ("Prime Market",)
KRX_SELECTED_MARKETS = ("KOSPI",)


def _region_suffixes(region_key: str) -> tuple[str, ...]:
    """The region's own Yahoo suffixes from REGION_META (never hardcoded)."""
    return tuple(s for s in universe_regions.REGION_META[region_key]["suffixes"]
                 if s)


def region_member_ok(region_key: str, member: str) -> bool:
    """True iff ``member`` is a well-shaped member of ``region_key``.

    Root = member with ONE of the region's own REGION_META suffixes stripped
    (accepted members re-parse); a foreign dotted tail is never stripped.
    The root must fullmatch the region's digit-shape (TSE 4 / KRX 6).
    Rejects: 5-digit TSE forms, the 7-digit `0005930.KS` class, letter
    codes (JPX ETN `130A`, KRX preferred `0203K0`) and foreign non-members
    (`700.HK`) alike."""
    shape = SHAPES.get(region_key)
    if shape is None:
        return False
    root = member
    for suf in _region_suffixes(region_key):
        if member.endswith(suf) and len(member) > len(suf):
            root = member[: -len(suf)]
            break
    return shape.fullmatch(root) is not None


def _read_roster(path: Path) -> list[tuple[str, str, str]]:
    """Roster CSV = '#' provenance comments + header + code,segment,name."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path}: in-repo exchange roster missing — re-land it with "
            ".audits fetch_landing.py (MC 10718)")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    rows = list(csv.reader(lines))
    return [(r[0], r[1], r[2] if len(r) > 2 else "") for r in rows[1:]]


def _collect(rows, shape: re.Pattern, is_selected, suffix_for):
    """Apply the code-shape rule to RAW roster rows, then the selection.

    Returns (sorted unique resolve-shaped members, rejected raw codes).
    Rejection happens BEFORE selection: malformed codes are counted even in
    unselected segments — the counts RESULT.md reports are the parser's."""
    kept: set[str] = set()
    rejected: list[str] = []
    for code, segment, *_ in rows:
        if not shape.fullmatch(code):
            rejected.append(code)
            continue
        if is_selected(segment):
            kept.add(code + suffix_for(segment))
    return sorted(kept), rejected


def build_listing_regions(tse_path: Path | None = None,
                          krx_path: Path | None = None
                          ) -> dict[str, list[str]]:
    """Return {"japan": [...], "korea": [...]} resolve-shaped members from
    the two in-repo rosters (host-reachable: no /srv root, no reconcile_m3,
    no network). Every member passes apply_region_suffix UNCHANGED (its
    rule 1 keeps an already-suffixed member verbatim)."""
    jp_suffix = universe_regions.REGION_META["japan"]["suffixes"][0]
    kr_sufs = universe_regions.REGION_META["korea"]["suffixes"]
    # KRX market -> Yahoo suffix: .KS primary (KOSPI), .KQ second (KOSDAQ)
    # — both read from the REGION_META tuple, nothing invented here.
    krx_suffix_by_market = {"KOSPI": kr_sufs[0], "KOSDAQ": kr_sufs[1]}

    tse_rows = _read_roster(tse_path or TSE_FILE)
    krx_rows = _read_roster(krx_path or KRX_FILE)

    japan, tse_rej = _collect(
        tse_rows, SHAPES["japan"],
        lambda seg: seg.startswith(TSE_SELECTED_SEGMENTS),
        lambda seg: jp_suffix)
    korea, krx_rej = _collect(
        krx_rows, SHAPES["korea"],
        lambda seg: seg in KRX_SELECTED_MARKETS,
        lambda seg: krx_suffix_by_market[seg])

    total = len(japan) + len(korea)
    if total > LISTING_BUDGET_TOTAL:
        raise ValueError(
            f"selected listing size {total} (japan {len(japan)} + korea "
            f"{len(korea)}) exceeds the page headroom budget "
            f"{LISTING_BUDGET_TOTAL} — widen nothing; shrink the selection "
            f"(MC 10718)")
    logger.info("MC 10718 listing source: TSE raw %d rejected %d -> japan %d;"
                " KRX raw %d rejected %d -> korea %d", len(tse_rows),
                len(tse_rej), len(japan), len(krx_rows), len(krx_rej),
                len(korea))
    return {"japan": japan, "korea": korea}
