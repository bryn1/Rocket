"""MC 10761: the US class-share REQUEST form and the round trip back.

Both halves are asserted, because half a fix is a second bug: a registry key
like ``BRK/A`` must be REWRITTEN on the request (Yahoo 404s the slash form)
and the frame must land keyed by ``BRK/A`` again — the store filename stays
what ``full_universe.cache_filename`` derives from the registry key
(``BRK_A.csv``), and the production walk can still resolve the member (its
region map is keyed by the registry symbol, so a frame under ``BRK-B`` dies
in ``fetch_store.absorb`` and books the member NOT_FETCHED).

Hermetic: no network. The Yahoo side is the established seam — the real
``_DownloadCtx`` with a scripted ``_download_impl`` (test_bulk_fetcher.py);
the store half drives ``fetch_store.run_fetch`` through the REAL
``fetch_batch_typed`` into ``store_io`` (the only writer) under a tmp cache
dir. The live proof is on the card's RESULT.md.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

TESTS = Path(__file__).resolve().parent
SOURCE = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "scripts"))

import fu_fixtures as fx                                             # noqa: E402
import yfinance.multi as ym                                          # noqa: E402
import rocket.data.bulk_fetcher as bf                                # noqa: E402
import rocket.data.class_share as cs                                 # noqa: E402
from full_universe import cache_filename                             # noqa: E402
from rocket.data.bulk_fetcher import fetch_batch_typed               # noqa: E402
from rocket.data.class_share import (land_results, request_batch,    # noqa: E402
                                     request_symbol)

UNIVERSE = SOURCE / "rocket" / "data" / "universe_cache.json"
# The twelve this card was written for (us_tickers.csv: Berkshire A+B,
# Brown-Forman A+B, HEICO-A, Haverty-A, AKOIR A+B, Bio-Rad B, Courtland A+B,
# WiseTech B). A subset pin, never an exact count: the registry is another
# card's data (MC 10738) and this test must not ride its churn.
TWELVE = ["AKO/A", "AKO/B", "BF/A", "BF/B", "BIO/B", "BRK/A", "BRK/B",
          "CRD/A", "CRD/B", "HEI/A", "HVT/A", "WSO/B"]
PROD_NOT_FOUND = ('possibly delisted; no price data found  (period=1y) '
                  '(Yahoo error = "No data found, symbol may be delisted")')


def registry_class_shares() -> set[str]:
    """The tracked registry's own slash-carrying members, read-only."""
    doc = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    return {s for v in doc["tickers"].values() for s in v if "/" in s}


def multi(tickers, days=5):
    """A yfinance group_by='ticker' frame: MultiIndex level 0 carries the
    symbols that were REQUESTED — exactly why the keys must be mapped back."""
    idx = pd.date_range("2026-01-01", periods=days)
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low",
                                                 "Close", "Volume"]])
    return pd.DataFrame(10.0, index=idx, columns=cols)


def _scripted_impl(monkeypatch, dead=()):
    """_download_impl double: lands every requested member except `dead`,
    which errors with the PRODUCTION not-found string."""
    calls = []

    def fake_impl(ctx, tickers, **kw):
        calls.append(list(tickers))
        for t in dead:
            ctx.errors[t] = PROD_NOT_FOUND
        return multi([t for t in tickers if t not in dead])

    monkeypatch.setattr(ym, "_download_impl", fake_impl)
    return calls


# ── half 1: the request form, narrow by construction ───────────────────────
def test_registry_class_shares_become_yahoo_class_shares():
    found = registry_class_shares()
    assert set(TWELVE) <= found, sorted(set(TWELVE) - found)
    for s in TWELVE:
        root, cls = s.split("/")
        assert request_symbol(s) == f"{root}-{cls}", s
    assert request_symbol("BRK/A") == "BRK-A"
    assert request_symbol("WSO/B") == "WSO-B"


def test_only_the_us_class_share_shape_is_rewritten():
    """Every REFUSED shape passes through VERBATIM: a generic slash-to-hyphen
    substitution would invent tickers for symbols that never had a class."""
    refused = ["brk/a",                              # lower case
               "BRK/AB", "BRK/ABC",                  # multi-letter class
               "123/A", "3M/A",                      # digit in the root
               "BRK/A/B", "/B", "BRK/", "/", "A/B/C",  # not the shape
               "ABCDEFGHI/A",                        # root longer than 5
               "BRK/A ", " BRK/A", "BRK /A", "BRK-A",  # blanks, already hyphen
               "SAAB-B.ST", "SAP.DE", "GIB.A", "600519.SS", "AAPL",
               "BRK/A.ST",                           # class AND exchange tail
               ""]
    for s in refused:
        assert request_symbol(s) == s, s


def test_request_batch_dedupes_requests_and_keeps_every_owner():
    requests, owner = request_batch(["AAPL", "BRK/A", "BF/A", "BRK/A"])
    assert requests == ["AAPL", "BRK-A", "BF-A"]      # de-duped, member order
    assert owner == {"AAPL": "AAPL", "BRK/A": "BRK-A", "BF/A": "BF-A"}


def test_land_results_keys_frames_to_the_registry_symbols():
    frames = {"BRK-A": pd.DataFrame({"close": [1.0]}),
              "AAPL": pd.DataFrame({"close": [2.0]})}
    _, owner = request_batch(["BRK/A", "AAPL"])
    landed = land_results(frames, owner)
    assert set(landed) == {"BRK/A", "AAPL"}
    names = {cache_filename(k) for k in landed}
    assert names == {"BRK_A.csv", "AAPL.csv"}          # the nightly's own name
    # a key no member asked for passes through under its own name:
    assert set(land_results({"ZZZ": pd.DataFrame({"close": [3.0]})},
                            owner)) == {"ZZZ"}


# ── half 2: the seam itself, both arms ─────────────────────────────────────
def test_seam_requests_the_form_and_returns_the_registry_key(monkeypatch):
    calls = _scripted_impl(monkeypatch, dead=["BRK-A"])
    res, typed = fetch_batch_typed(["BRK/A", "BRK/B", "AAPL"], "1y")
    assert calls == [["BRK-A", "BRK-B", "AAPL"]]      # the form goes out
    assert set(res) == {"BRK/B", "AAPL"}              # the key comes back
    assert typed == {"BRK/A": "not_found"}    # booked on BRK/A, never BRK-A


def test_seam_error_channel_never_leaks_the_request_form(monkeypatch):
    """A silent loss (frame deleted by the gate, no ctx.errors entry) stays
    'undecidable' and is still booked against the registry symbol."""
    monkeypatch.setattr(ym, "_download_impl",
                        lambda ctx, tickers, **kw: multi([tickers[1]]))
    res, typed = fetch_batch_typed(["BRK/A", "BF/B"], "1y")
    assert set(res) == {"BF/B"}
    assert typed == {"BRK/A": "undecidable"}


def test_capture_loss_fallback_lands_the_registry_key_too(monkeypatch):
    """The public yf.download arm (a yfinance bump) keeps the same contract:
    the form goes out, the registry key comes back."""
    monkeypatch.delattr(ym, "_download_impl")
    sent = {}

    def dl(**kw):
        sent.update(kw)
        return multi(kw["tickers"])

    monkeypatch.setattr(bf.yf, "download", dl)
    res, _ = fetch_batch_typed(["BRK/A", "SAP.DE"], "1y")
    assert sent["tickers"] == ["BRK-A", "SAP.DE"]
    assert set(res) == {"BRK/A", "SAP.DE"}


# ── the whole chain: seam -> fetch_store -> store_io (the ONLY writer) ─────
def _run_chain(monkeypatch, tmp_path):
    import fetch_store
    import full_universe as fu

    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    monkeypatch.setattr(fu, "make_batches", lambda ts, size=50: [list(ts)])
    plan = fu.UniversePlan(regions={"usa": ["BRK/A", "BRK/B"]},
                           order=["usa"], m_unique=2, dropped=[],
                           registry_ts=fx.FIXTURE_TS)
    errors: list[str] = []
    facts = fetch_store.run_fetch(plan, skip_fetch=False,
                                  now_fn=fx.fixed_utc(fx.today()),
                                  cache_dir=tmp_path, errors=errors)
    return facts, errors


def test_nightly_chain_writes_the_registry_filename(monkeypatch, tmp_path):
    """run_fetch (the production walk) + the REAL fetch_batch_typed +
    store_io: both class shares land as BRK_A.csv / BRK_B.csv — the names
    cache_filename derives from the registry keys — with zero store errors."""
    _scripted_impl(monkeypatch)
    facts, errors = _run_chain(monkeypatch, tmp_path)
    assert errors == []
    assert all(f["batch_completed"] for f in facts.values()), facts
    assert sorted(p.name for p in tmp_path.iterdir()) == ["BRK_A.csv",
                                                          "BRK_B.csv"]
    assert all(len(pd.read_csv(p)) > 0 for p in tmp_path.iterdir())


# ── planted-bad RED proofs: the green tests bite on the RULE ───────────────
def test_planted_bad_permissive_rule_invents_tickers(monkeypatch):
    """Shape rule planted permissive (any slash, any class) => the shapes the
    card REFUSES are rewritten: 'BRK/AB' -> 'BRK-AB', '123/A' -> '123-A',
    'BRK/A.ST' -> 'BRK-A.ST', and 'BRK/A/B' -> 'BRK/A-B' (invention that does
    not even leave a valid symbol). The seam SENDS them. RED pair of
    test_only_the_us_class_share_shape_is_rewritten — that test passes
    because of the rule, not fixture luck."""
    monkeypatch.setattr(cs, "_CLASS_SHARE", re.compile(r"\A(.+)/(.+)\Z"))
    assert cs.request_symbol("BRK/AB") == "BRK-AB"
    assert cs.request_symbol("123/A") == "123-A"
    assert cs.request_symbol("BRK/A/B") == "BRK/A-B"
    assert cs.request_symbol("BRK/A.ST") == "BRK-A.ST"
    calls = _scripted_impl(monkeypatch)
    fetch_batch_typed(["BRK/AB", "123/A"], "1y")
    assert calls == [["BRK-AB", "123-A"]]


def test_planted_bad_identity_landing_loses_the_round_trip(monkeypatch,
                                                           tmp_path):
    """Landing planted back to the pre-card identity (frames keep Yahoo's key)
    => the production chain writes NOTHING, books both members NOT_FETCHED and
    reports a store error per member. RED pair of the round-trip tests: the
    filename coincidence ('BRK-B' also escapes to BRK_B.csv) is NOT what the
    nightly needs — the walk resolves the member by its REGISTRY key."""
    _scripted_impl(monkeypatch)
    monkeypatch.setattr(bf, "land_results", lambda frames, owner: frames)
    facts, errors = _run_chain(monkeypatch, tmp_path)
    assert list(tmp_path.iterdir()) == []                 # nothing landed
    assert not any(f["batch_completed"] for f in facts.values())
    # 2 members x 2 rounds (the main walk + the single §5 re-queue), each a
    # KeyError on the member's region because the frame carries the wrong key:
    assert len(errors) == 4 and all("store:" in e for e in errors), errors
