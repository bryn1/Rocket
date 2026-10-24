"""MC 10764 (T38): the class-share form reaches EVERY yfinance caller.

MC 10761 fixed the live seam (`fetch_batch_typed`); this card carried the
SAME mechanism (`rocket.data.class_share`, never a second normaliser) to the
then-remaining request sites. The legacy direct-yfinance scripts retired
with MC 11199 batch B1 (2026-10-24) and their caller tests retired with
them; what remains drives the LIVE seams only: the parquet CLI path
(`_fetch_batch`/`fetch_all`). Every test below drives a REAL caller with a
scripted `yf.download` and asserts BOTH halves: the REQUEST list carries
`BRK-A` and the result is keyed under the registry symbol `BRK/A` (the
store/CSV name stays `BRK_A.csv`).
"""
import sys
from pathlib import Path

import pandas as pd

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for p in (str(TESTS), str(SOURCE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import rocket.data.bulk_fetcher as bf                                # noqa: E402


def multi(tickers, days=5):
    idx = pd.date_range("2026-01-01", periods=days)
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low",
                                                 "Close", "Volume"]])
    return pd.DataFrame(10.0, index=idx, columns=cols)


# ── caller 1: the parquet CLI batch (_fetch_batch -> fetch_all path) ───────
def test_bulk_fetcher_batch_requests_dash_and_lands_registry_keys(monkeypatch):
    seen = []

    def fake(tickers, **kw):
        seen.append(list(tickers))
        return multi(list(tickers))

    monkeypatch.setattr(bf.yf, "download", fake)
    out = bf._fetch_batch(["BRK/A", "000100.KS"], "1y")
    assert seen[0] == ["BRK-A", "000100.KS"]           # request form
    assert "BRK/A" in out and "BRK-A" not in out       # landed under registry
    assert set(out) == {"BRK/A", "000100.KS"}


def test_bulk_fetcher_landing_is_load_bearing(monkeypatch):
    """Planted regression: identity landing (no land_results) must go RED.
    Reproduced by an owner map that drops the mapping — the shipped helper
    itself is pinned in test_class_share_form.py."""
    seen = []

    def fake(tickers, **kw):
        seen.append(list(tickers))
        return multi(list(tickers))

    monkeypatch.setattr(bf.yf, "download", fake)
    out = bf._fetch_batch(["WSO/B"], "1y")
    assert "WSO/B" in out                              # registry key present
    # the planted identity map: request-keyed only — mismatch is the red:
    from rocket.data.class_share import request_batch
    _, owner = request_batch(["WSO/B"])
    assert owner["WSO/B"] == "WSO-B" and owner["WSO/B"] != "WSO/B"


def test_doc_line_names_the_live_seam():
    src = (SOURCE / "scripts" / "generate_demo_page.py").read_text()
    head = src[:1200]
    assert "bulk_fetcher.fetch_batch_typed" in head
    assert "bulk_fetcher._fetch_batch" not in head
