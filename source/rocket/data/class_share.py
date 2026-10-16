"""US class-share REQUEST form, and the inverse that keys results back.

MC 10761. The registry keys twelve valid US share classes with a slash
(``BRK/A`` = Berkshire Hathaway Inc., NYSE) while Yahoo's class-share form is
``BRK-B``; the slash form 404s ("unexpected character: line 1 column 1"). So
the rewrite happens on the REQUEST ONLY, and ``land_results`` maps every
returned frame back to the registry symbol that asked for it — the store
filename stays what ``full_universe.cache_filename`` derives from the registry
key (``BRK/A`` -> ``BRK_A.csv``), never ``BRK-B.csv``.

Narrow BY CONSTRUCTION — the card forbids a broad normaliser. Only
``\\A[A-Z]{1,5}/[A-Z]\\Z`` is rewritten (root: 1-5 upper-case letters, one
slash, one upper-case class letter). Everything else passes through VERBATIM,
so no other market's symbol can ever gain an invented ticker:

  rewritten  BRK/A  BF/B  HEI/A  WSO/B
  refused    brk/a (lower case)      BRK/AB (multi-letter class)
             123/A  (digit root)     BRK/A/B, /B, BRK/  (slash shape)
             SAAB-B.ST, SAP.DE, GIB.A, 600519.SS (no slash / dotted form)

``apply_region_suffix`` (MC 10342) is the suffix choke point and is NOT
consulted or duplicated here: this maps a US symbol's class separator, which
never carries an exchange suffix.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

_CLASS_SHARE = re.compile(r"\A([A-Z]{1,5})/([A-Z])\Z")


def request_symbol(symbol: str) -> str:
    """Registry symbol -> the symbol to request. Identity when refused."""
    m = _CLASS_SHARE.match(symbol)
    return symbol if m is None else f"{m.group(1)}-{m.group(2)}"


def request_batch(symbols: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """(requests, owner): de-duplicated request list in member order, plus
    every member's request symbol (identity for refused members)."""
    owner = {s: request_symbol(s) for s in symbols}
    return list(dict.fromkeys(owner.values())), owner


def land_results(results: Dict[str, object],
                 owner: Dict[str, str]) -> Dict[str, object]:
    """Frames keyed by REQUEST symbol -> frames keyed by the REGISTRY symbol
    that asked for it (several owners share one frame; an unexpected key
    passes through under its own name, exactly as before this mapping)."""
    owners: Dict[str, List[str]] = {}
    for registry, request in owner.items():
        owners.setdefault(request, []).append(registry)
    landed: Dict[str, object] = {}
    for request, frame in results.items():
        for registry in owners.get(request, [request]):
            landed[registry] = frame
    return landed
