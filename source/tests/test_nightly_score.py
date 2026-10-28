"""MC 11546 (B1) — nightly adapter: explicit social policy + timeout plumbing.

The six named tests of PLAN-c3 §6 B1 / brief-b1 DoD, all httpx-monkeypatched
(no network):

(a) C2 CLAUSE (BINDING, folded verbatim): compute_rocket_score(...,
    social_sentiment=True) WITHOUT social_timeout must reach httpx.post with
    timeout=30.0 unchanged — the CAPTURED call kwarg is asserted ("no
    exception" is not the assertion);
(b) the adapter path never hands httpx a None timeout (social=True ⇒ captured
    kwarg == 2.0 = nightly_score.SOCIAL_TIMEOUT_S);
(c) social=False ⇒ zero httpx calls captured, with the POSITIVE CONTROL that
    the same captured-kwarg transport DOES record a direct social_sentiment=True
    call (the zero-assertion can fail);
(d) hanging-listener emulation with social=True returns within ~N, bounded by
    the adapter-set timeout, not 30 s;
(e) the golden-35 fixture CSVs score DETERMINISTICALLY twice via the adapter
    with explicit social=False;
(f) row keys equal the set score_all.py:72-77 consumes today.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest

TESTS = Path(__file__).resolve().parent
for _p in (str(TESTS), str(TESTS.parent / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nightly_score                                     # noqa: E402
from demo_page_fixtures import GOLDEN35_DIR              # noqa: E402
from rocket.data.models import Region, TickerInfo        # noqa: E402
from rocket.scoring.rocket_score import compute_rocket_score  # noqa: E402


def _recording_transport(monkeypatch):
    """Monkeypatch httpx.post; return the captured-call list.

    status_code != 200 makes scrape_r_wallstreetbets return [] immediately —
    the sentiment path completes fast on captured kwargs, zero network.
    """
    calls: list[dict] = []

    class _Resp:
        status_code = 500

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append({"url": url, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


@pytest.fixture(scope="module")
def aapl_df() -> pd.DataFrame:
    return pd.read_csv(GOLDEN35_DIR / "AAPL.csv")


def _ticker_info(ticker: str) -> TickerInfo:
    return TickerInfo(ticker=ticker, name=ticker, region=Region.US)


# ── (a) C2 CLAUSE — the binding captured-kwarg assertion ───────────────────

def test_c2_direct_call_reaches_httpx_with_30_timeout(aapl_df, monkeypatch):
    """C2 verbatim: WITHOUT social_timeout httpx.post is reached with
    timeout=30.0 unchanged — asserted off the CAPTURED call kwarg."""
    calls = _recording_transport(monkeypatch)
    compute_rocket_score(aapl_df, _ticker_info("AAPL"),
                         current_price=100.0, social_sentiment=True)
    assert len(calls) == 1, "social_sentiment=True must reach the scrape once"
    assert calls[0]["timeout"] == 30.0, (
        "C2 pin: the library default 30.0 must survive a direct call that "
        "supplies no social_timeout (captured kwarg, not 'no exception')")


# ── (b) adapter social=True ⇒ captured timeout == N, never None ────────────

def test_adapter_social_true_supplies_named_2s_and_never_none(aapl_df,
                                                              monkeypatch):
    calls = _recording_transport(monkeypatch)
    nightly_score.score_row(aapl_df, "AAPL", "usa", social=True)
    assert len(calls) == 1, "social=True must reach the scrape once"
    assert all(c["timeout"] is not None for c in calls), (
        "C2 adapter half: a None timeout may never reach httpx")
    assert calls[0]["timeout"] == nightly_score.SOCIAL_TIMEOUT_S == 2.0, (
        "adapter must supply the named N = 2.0 s (D2 safety bound)")


# ── (c) social=False ⇒ zero httpx calls, plus the positive control ─────────

def test_adapter_social_false_fires_no_httpx(aapl_df, monkeypatch):
    calls = _recording_transport(monkeypatch)
    row = nightly_score.score_row(aapl_df, "AAPL", "usa", social=False)
    assert row["ticker"] == "AAPL", "the row must still be scored and returned"
    assert calls == [], "the adapter social=False path must never fire httpx"
    # POSITIVE CONTROL (B1 DoD): the SAME transport records a direct
    # social_sentiment=True call — the zero-assertion above CAN fail.
    compute_rocket_score(aapl_df, _ticker_info("AAPL"),
                         current_price=100.0, social_sentiment=True)
    assert len(calls) == 1, (
        "positive control: the captured-kwarg transport must see the "
        "sentiment path fire when social is ON, else the zero-assertion "
        "is vacuous")


# ── (d) hanging listener bounded by the adapter-set N ──────────────────────

def test_hanging_listener_bounded_by_adapter_timeout(aapl_df, monkeypatch):
    """Emulated hanging listener: the fake honours the passed `timeout`
    exactly like real httpx does (raises TimeoutException after it), so this
    pins THAT the adapter bounds the call at N = 2.0 s — not that httpx
    enforces its own parameter. Fail-neutral: social_score stays 0.0 and the
    row still comes back."""
    def hanging_post(url, json=None, timeout=None, **kw):
        deadline = time.monotonic() + (timeout if timeout is not None
                                       else 30.0)
        while True:
            time.sleep(0.05)
            if time.monotonic() >= deadline:
                raise httpx.TimeoutException("hanging listener (emulated)")

    monkeypatch.setattr(httpx, "post", hanging_post)
    t0 = time.monotonic()
    row = nightly_score.score_row(aapl_df, "AAPL", "usa", social=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, (
        f"adapter path took {elapsed:.1f}s — hanging listener was bounded by "
        "something other than the adapter's N = 2.0 s (library 30.0 = FAIL)")
    assert row["ticker"] == "AAPL", "timeout must stay fail-neutral: row kept"


# ── (e) golden-35 deterministic twice through the adapter ──────────────────

def test_golden35_deterministic_twice_via_adapter():
    csvs = sorted(GOLDEN35_DIR.glob("*.csv"))
    assert len(csvs) == 35, f"golden-35 fixture store broken: {len(csvs)} CSVs"
    for path in csvs:
        df = pd.read_csv(path)
        first = nightly_score.score_row(df, path.stem, "us", social=False)
        second = nightly_score.score_row(df, path.stem, "us", social=False)
        assert first == second, (
            f"{path.stem}: adapter row must be byte-identical when scored "
            f"twice: {first!r} != {second!r}")


# ── (f) row keys equal score_all.py:72-77 exactly ──────────────────────────

def test_row_keys_equal_score_all_today(aapl_df):
    """score_all.py:72-77 today: meta["row"] carries exactly
    {ticker, signal, overall, momentum, trend, volatility, volume, close}."""
    row = nightly_score.score_row(aapl_df, "AAPL", "usa", social=False)
    assert set(row) == {"ticker", "signal", "overall", "momentum", "trend",
                        "volatility", "volume", "close"}
    assert row["signal"] in ("BUY", "SELL", "HOLD"), (
        "demo_render._signal_class (:103-104) keys on BUY/SELL/else-hold")
