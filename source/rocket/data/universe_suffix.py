"""Region Yahoo-suffix hygiene for the universe builder (MC 10342).

One concern: map a region list's members to Yahoo RESOLVE-SHAPE so a
builder regeneration can never revert the T25 registry hygiene (commit
761e26e). The fetch path (bulk_fetcher) sends registry symbols VERBATIM to
yfinance, so a suffix-less member is unsolvable-by-construction. Suffixes
are read from REGION_META only (universe_regions is the single source of
truth; nothing here hardcodes one).

Mapping rules (reverse-engineered from the T25-committed buckets, which are
the byte-comparison ground truth):
  * member already ends with one of the region's REGION_META suffixes
    (any of the tuple: canada .TO/.V, china .SS/.SZ, ...) -> kept as-is;
  * member carries a FOREIGN exchange-ish tail (a dotted tail that is not
    a TSX class/unit indicator, e.g. the embedded Swiss literals NESN.SZ /
    2000.SN) -> tail replaced by the region's PRIMARY suffix (.SW) —
    T25 verified Swiss members as .SW;
  * bare member, or dotted TSX class/unit root (tails A/B single-class or
    UN unit, kept by T25: GIB.A -> GIB.A.TO, AP.UN -> AP.UN.TO) -> PRIMARY
    suffix appended, root untouched;
  * regions whose REGION_META primary is None (usa, international) and
    region keys absent from REGION_META pass through — nothing is invented.

The mapping is idempotent: mapped output satisfies rule 1 for every member.
"""

from __future__ import annotations

import re

from . import universe_regions

# TSX class/unit indicators: part of the ROOT, not an exchange suffix.
# Evidence (T25 committed canada bucket): GIB.A.TO, BBD.B.TO, AP.UN.TO,
# ACO.X.TO — root kept, primary appended. Multi-letter tails that are not
# units (SZ/SN/...) are SIX/Yahoo exchange-ish and get replaced instead.
_CLASS_UNIT_TAIL_RE = re.compile(r"\.[A-Z]$|\.UN$")


def apply_region_suffix(region_key: str, symbols) -> list[str]:
    """Return sorted unique Yahoo resolve-shaped members for ``region_key``.

    See module docstring for the mapping rules; usa/international pass
    through (REGION_META primary None)."""
    meta = universe_regions.REGION_META.get(region_key)
    if meta is None:
        return sorted(set(symbols))
    suffixes = meta["suffixes"]
    if suffixes[0] is None:            # usa / international aggregate
        return sorted(set(symbols))
    primary = suffixes[0]
    out: set[str] = set()
    for sym in symbols:
        if any(sym.endswith(suf) for suf in suffixes):
            out.add(sym)                                   # already resolve-shaped
        elif "." in sym and not _CLASS_UNIT_TAIL_RE.search(sym):
            out.add(sym.rsplit(".", 1)[0] + primary)       # foreign exchange tail
        else:
            out.add(sym + primary)                         # bare root / class-unit root
    return sorted(out)
