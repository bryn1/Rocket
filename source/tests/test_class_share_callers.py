"""MC 10764 (T38): the class-share form reaches EVERY yfinance caller.

MC 10761 fixed the live seam (`fetch_batch_typed`); this card carries the
SAME mechanism (`rocket.data.class_share`, never a second normaliser) to the
remaining request sites: the parquet CLI path (`_fetch_batch`/`fetch_all`)
and the legacy direct-yfinance scripts. Every test below drives a REAL
caller with a scripted `yf.download` / `yf.Ticker` and asserts BOTH halves:
the REQUEST list carries `BRK-A` and the result is keyed under the registry
symbol `BRK/A` (the store/CSV name stays `BRK_A.csv`).
"""
import sys
from pathlib import Path

import pandas as pd

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
for p in (str(TESTS), str(SOURCE), str(SOURCE / "scripts")):
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


def _load(name):
    # the legacy CLIs chdir to the BUILD root at import; load them by path so
    # the hermetic run never depends on that build dir existing.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, SOURCE / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    before = Path.cwd()
    import os
    real_chdir = os.chdir
    # the legacy CLIs are module-level singletons with a build-root chdir;
    # hermetic run: keep cwd, the module is pure-function otherwise.
    os.chdir = lambda p: None
    try:
        spec.loader.exec_module(mod)
    finally:
        os.chdir = real_chdir
        os.chdir(str(before))
    sys.modules[name] = mod
    return mod


# ── caller 2: daily_push.batch_fetch_history ───────────────────────────────
def test_daily_push_batch_history_dash_request_and_registry_keys(monkeypatch):
    dp = _load("daily_push")
    seen = []

    def fake(tickers, **kw):
        seen.append(list(tickers))
        return multi(list(tickers))

    monkeypatch.setattr(dp.yf, "download", fake)
    out = dp.batch_fetch_history(["BRK/A", "BF/B"], {})
    assert seen[0] == ["BRK-A", "BF-B"]
    assert set(out) == {"BRK/A", "BF/B"}


# ── caller 3: scanall live-detection batch ─────────────────────────────────
def test_scanall_batch_matches_owner_form(monkeypatch):
    scanall = _load("scanall")
    seen = []

    def fake(tickers, **kw):
        seen.append(list(tickers))
        return multi(list(tickers))

    monkeypatch.setattr(scanall.yf, "download", fake)
    from rocket.data.class_share import request_batch
    names = ["BRK/A", "000100.KS"]
    requests, owner = request_batch(names)
    df = scanall.yf.download(requests, period="1mo", group_by="ticker")
    tset = set(df.columns.get_level_values(0).unique())
    assert [n for n in names if owner.get(n, n) in tset] == names


# ── caller 4/5: single-ticker seams take the request form ──────────────────
def test_single_ticker_seams_use_request_form():
    from rocket.data.class_share import request_symbol
    assert request_symbol("BRK/A") == "BRK-A"
    for name in ("fetch_history", "scan_batch", "scanall", "daily_push"):
        text = (SOURCE / "scripts" / f"{name}.py").read_text()
        assert "request_symbol" in text, name


def test_doc_line_names_the_live_seam():
    src = (SOURCE / "scripts" / "generate_demo_page.py").read_text()
    head = src[:1200]
    assert "bulk_fetcher.fetch_batch_typed" in head
    assert "bulk_fetcher._fetch_batch" not in head
