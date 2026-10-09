"""MC 10309 (T18): stage-1 fetch honesty + rate-limit backoff — hermetic,
fake fetcher through the existing fetcher seam, no network, RUN-RELATIVE
dates. Cases per the fix dispatch:
(a) PARTIAL-STARVE: a non-empty batch that drops members must NOT mark them
    completed (old code green-lied: sibling presence marked all 50 live);
(b) LADDER: consecutive rate-limit signs step 0.5 -> 1 -> 2 -> 4 -> 8 -> 10 s,
    reset after 5 clean batches, one COOLDOWN before the re-queue round;
(c) REGRESSION: clean steady state is byte-identical behavior (BATCH_DELAY
    sleeps, no cooldown, no STARVED line);
(d) honest DEAD_AT_FETCH: frame fetched but store death — the one class that
    legitimately keeps batch_completed=True without a file (§4 #4);
(e) T20 (MC 10311): the end-of-stage RE-QUEUE round is paced by the SAME
    rule and the SAME ladder state as the main walk (one walk conceptually),
    the main-walk sleep sequence stays byte-identical, COOLDOWN stays
    exactly-once, and a re-queue event climbs from the rung the walk stopped at.
(f) T22 (MC 10312): the fetcher contract IS the production typed seam
    `(res, typed)` — a typed rate_limited answer (which NEVER raises, the
    live 429 shape that left the ladder inert) steps the SAME RL_LADDER,
    facts carry the additive typed key (final observation wins, a landing
    dominates with None), and the stats out-param + capture canary journal
    the night. Raising-fetcher tests STAY: the crash arm is still real.
"""
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import test_demo_page_generator as dtgen            # noqa: E402
import full_universe as fu                          # noqa: E402
import store_io                                     # noqa: E402
import fetch_store as fetch_stage                   # noqa: E402
from demo_page_fixtures import (                    # noqa: E402
    FRESH_BAR, NOW, mk_frame as _mk_frame, mk_row as _mk_row)

gen = dtgen.gen


class YFRateLimitError(Exception):
    """The known live class (yfinance): matched by CLASS NAME, not type."""


def _rl():
    return YFRateLimitError("Too Many Requests: Rate Limited. Trying again later")


def _plan(tickers):
    return fu.UniversePlan(regions={"usa": list(tickers)},
                           order=["usa"], m_unique=len(tickers))


def _stub(produced):
    """score_store double: rows + meta for `produced` tickers only."""
    results = {"usa": [_mk_row(t) for t in produced]}
    return {"results": results,
            "meta": {t: {"row": _mk_row(t), "has_usable_store": True,
                         "last_bar": FRESH_BAR} for t in produced},
            "last_bars": {"usa": [FRESH_BAR] * len(results["usa"])}}


@pytest.fixture
def hot_env(monkeypatch):
    """Sleep recorder + loud seams: BATCH_DELAY pinned to 0.5, COOLDOWN to a
    sentinel so its presence/absence in the recorded list IS the assertion."""
    import rocket.data.bulk_fetcher as bf
    monkeypatch.setattr(bf, "BATCH_DELAY", 0.5)
    monkeypatch.setattr(fetch_stage, "COOLDOWN", 999.0, raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ── (a) PARTIAL-STARVE ───────────────────────────────────────────────────────

def test_partial_batch_starved_members_are_not_completed(tmp_path, hot_env,
                                                         capsys):
    """RED-UNDER-NEW against the old code: res={S1} of [S1,S2,S3] marked ALL
    three completed -> not_fetched==0 green on a lie. Honest: S2,S3 stay
    False, are re-queued as their own mini-batch, and while fileless are
    loud NOT_FETCHED (guard refuses) + STARVED-NOT-FETCHED n=2."""
    plan = _plan(["S1", "S2", "S3"])

    def fetcher(batch, period):            # production shape: (res, typed)
        return ({t: _mk_frame() for t in batch if t == "S1"}, {})

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher)
    assert facts["S1"]["batch_completed"] is True
    assert facts["S2"]["batch_completed"] is False   # the F-2 lie dies here
    assert facts["S3"]["batch_completed"] is False
    err = capsys.readouterr().err
    assert "STARVED-NOT-FETCHED n=2" in err
    assert (tmp_path / "S1.csv").exists() and not (tmp_path / "S2.csv").exists()
    assignments, _ = gen.build_accounting(plan, _stub(["S1"]), facts)
    reasons = store_io.accounting_reasons(assignments, plan.m_unique)
    assert any("not_fetched=2" in r and "S2" in r and "S3" in r for r in
               reasons)                    # §9 #3 refuses, honest


def test_starved_members_rescued_by_the_single_requeue(tmp_path, hot_env,
                                                       capsys):
    """Re-queue round (ONE, §5): starved members return -> completed, files
    on disk, no STARVED line, partition closes clean (guard passes)."""
    plan = _plan(["S1", "S2", "S3"])
    calls = []

    def fetcher(batch, period):
        calls.append(tuple(batch))
        if len(calls) == 1:                # first pass: throttle drops 2 of 3
            return {batch[0]: _mk_frame()}, {}
        return {t: _mk_frame() for t in batch}, {}

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher)
    assert all(f["batch_completed"] for f in facts.values())
    assert calls == [("S1", "S2", "S3"), ("S2", "S3")]   # own mini-batch
    err = capsys.readouterr().err
    assert "STARVED" not in err
    for t in ("S1", "S2", "S3"):
        assert (tmp_path / f"{t}.csv").exists()
    assignments, _ = gen.build_accounting(plan, _stub(["S1", "S2", "S3"]),
                                          facts)
    assert store_io.accounting_reasons(assignments, plan.m_unique) == []


def test_re_starved_member_counted_exactly_once(tmp_path, hot_env, capsys):
    """Mutation guard on the dedup: S3 is starved TWICE (main walk + the
    re-queue mini-batch also comes back without it); STARVED-NOT-FETCHED
    must count it once, never 2."""
    plan = _plan(["S1", "S2", "S3"])
    calls = []

    def fetcher(batch, period):
        calls.append(tuple(batch))
        got = {t: _mk_frame() for t in batch if t in ("S1", "S2")}
        if len(calls) == 1:
            got.pop("S2", None)                      # S2,S3 starved on round 1
        return got, {}                               # round 2: S2 lands, S3 not

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher)
    assert facts["S2"]["batch_completed"] is True    # rescued on round 2
    assert facts["S3"]["batch_completed"] is False
    err = capsys.readouterr().err
    assert "STARVED-NOT-FETCHED n=1" in err         # S3 once, not double


# ── (b) BACKOFF LADDER + cooldown ────────────────────────────────────────────

def test_backoff_seam_constants_are_module_seams():
    assert fetch_stage.RL_LADDER == (0.5, 1.0, 2.0, 4.0, 8.0, 10.0)
    assert fetch_stage.RL_RESET_AFTER == 5
    assert fetch_stage.COOLDOWN == 60.0


@pytest.fixture
def single_batch_env(monkeypatch, hot_env):
    """One-member batches (deterministic scripted stream) + shared hot_env."""
    monkeypatch.setattr(fu, "make_batches",
                        lambda ts, size=50: [[t] for t in ts])
    return hot_env


def _scripted(tmp_path, sleeps, script, extra=0, n=None, stats=None):
    """`n` tickers (default one per scripted call); the fetcher is scripted BY
    CALL INDEX, so script items past the main walk land IN the re-queue round.
    Scripted items are EXCEPTIONS — the crash arm (raising fetcher stays a
    real production arm: bulk_fetcher CAN still die mid-call)."""
    tickers = [f"TK{i}" for i in range(
        n if n is not None else len(script) + extra)]
    plan = _plan(tickers)
    calls = []

    def fetcher(batch, period):
        item = script[len(calls)] if len(calls) < len(script) else None
        calls.append(batch[0])
        if item is not None:
            raise item
        return {batch[0]: _mk_frame()}, {}     # production (res, typed) shape

    errors: list[str] = []
    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher,
                            errors=errors, stats=stats)
    return facts, errors, calls


def test_ladder_steps_cool_down_and_reset_after_five_clean(tmp_path,
                                                           single_batch_env):
    """RL,RL then 4 clean (NO reset yet) -> next event keeps climbing; 5
    consecutive clean batches reset to BATCH_DELAY -> next event back at 1 s.
    Reading (stated): the 5-clean reset IS the decay rule — a lone clean
    batch neither sleeps the ladder nor erases the streak. COOLDOWN (sentinel
    999) runs exactly once before the re-queue round when ANY event was seen."""
    sleeps = single_batch_env
    script = [_rl(), _rl(), None, None, None, None, _rl(),
              None, None, None, None, None, _rl()]
    facts, errors, calls = _scripted(tmp_path, sleeps, script)
    assert sleeps == [1.0, 2.0,                            # events 1, 2
                      0.5, 0.5, 0.5, 0.5,                  # 4 clean: no reset
                      4.0,                                 # event 3 climbs on
                      0.5, 0.5, 0.5, 0.5, 0.5,             # 5 clean: reset
                      1.0,                                 # event back to 1 s
                      999.0,                               # ONE cooldown
                      0.5, 0.5, 0.5, 0.5]                  # T20: re-queue paced
    assert sleeps.count(999.0) == 1                        # still exactly-once
    assert len(errors) == 4                    # events ride errors as before
    assert "Too Many Requests" in errors[0]
    # the four throttled batches were re-queued and succeeded there:
    assert all(f["batch_completed"] for f in facts.values())
    assert len(calls) == len(script) + 4


def test_non_rate_limit_error_keeps_generic_path(tmp_path, single_batch_env):
    """A plain exception does NOT step the ladder (sleeps BATCH_DELAY) but
    stays generic in errors; the RL streak persists (stated reading), and the
    cooldown still fired once because an event was seen."""
    sleeps = single_batch_env
    script = [_rl(), RuntimeError("planted non-RL boom"), _rl()]
    facts, errors, calls = _scripted(tmp_path, sleeps, script)
    assert sleeps == [1.0, 0.5, 2.0, 999.0, 0.5, 0.5, 0.5]
    assert any("planted non-RL boom" in e for e in errors)


# ── (e) T20 (MC 10311): the re-queue round is paced too ──────────────────────

def test_requeue_round_is_paced_and_the_ladder_crosses_the_boundary(tmp_path,
                                                                   single_batch_env,
                                                                   capsys):
    """RED-BEFORE (06): the re-queue round used to call the fetcher back-to-
    back with ZERO sleeps — exactly the calls most likely to 429. Same rule,
    same state: an event IN the round steps from the rung the main walk
    stopped at (streak 2 -> 4 s -> 8 s), and COOLDOWN stays exactly once,
    BEFORE the round — never a second one after more events (c)."""
    sleeps = single_batch_env
    facts, errors, calls = _scripted(tmp_path, sleeps, [_rl()] * 4, n=2)
    assert sleeps == [1.0, 2.0,                             # main walk rungs
                      999.0,                               # ONE cooldown
                      4.0, 8.0]                            # rungs CONTINUE
    assert sleeps.count(999.0) == 1
    assert len(calls) == 4 and len(errors) == 2            # round adds no errors
    assert all(f["batch_completed"] is False for f in facts.values())
    assert "BATCH-EMPTY-OR-DEAD" in capsys.readouterr().err


def test_clean_requeue_round_paced_main_walk_unchanged(tmp_path,
                                                      single_batch_env):
    """(a)+(b)+(c) together, clean re-queue: 2 events then 4 clean in the
    MAIN WALK (sequence identical to the pre-T20 pin, rungs and all), ONE
    cooldown, then the 2 re-queued batches sleep BATCH_DELAY like any other
    batch — one walk conceptually, not a second sleep mechanism."""
    sleeps = single_batch_env
    facts, errors, calls = _scripted(tmp_path, sleeps, [_rl(), _rl()], n=6)
    assert sleeps == [1.0, 2.0, 0.5, 0.5, 0.5, 0.5, 999.0, 0.5, 0.5]
    assert 999.0 == sleeps[6] and sleeps.count(999.0) == 1
    assert len(calls) == 8 and len(errors) == 2
    assert all(f["batch_completed"] for f in facts.values())


def test_clean_requeue_round_without_events_has_no_cooldown(tmp_path,
                                                            single_batch_env):
    """(c) the iff's zero side, WITH a re-queue round present: a {} batch
    re-queued without any rate-limit event sleeps BATCH_DELAY per batch and
    NEVER the cooldown sentinel — pacing must not become a backoff."""
    sleeps = single_batch_env
    plan = _plan(["TK0", "TK1", "TK2", "TK3"])
    calls = []

    def fetcher(batch, period):
        calls.append(batch[0])
        return (({}, {}) if len(calls) <= 2
                else ({batch[0]: _mk_frame()}, {}))

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher)
    assert sleeps == [0.5] * 6 and 999.0 not in sleeps   # 4 walk + 2 round
    assert len(calls) == 6
    assert all(f["batch_completed"] for f in facts.values())


# ── (c) REGRESSION: clean steady state unchanged ────────────────────────────

def test_clean_run_identical_to_today(tmp_path, single_batch_env, capsys):
    """No exceptions: sleeps are exactly BATCH_DELAY per main-walk batch, NO
    cooldown sentinel, no re-queue calls, no STARVED/BATCH-EMPTY lines."""
    sleeps = single_batch_env
    facts, errors, calls = _scripted(tmp_path, sleeps, [], extra=7)
    assert sleeps == [0.5] * 7
    assert 999.0 not in sleeps
    assert len(calls) == 7
    assert not errors
    assert all(f["batch_completed"] for f in facts.values())
    err = capsys.readouterr().err
    assert "STARVED" not in err and "BATCH-EMPTY-OR-DEAD" not in err


# ── (d) DEAD_AT_FETCH keeps ONE honest meaning (§4 #4) ───────────────────────

def test_store_death_after_frame_is_dead_at_fetch_not_starved(tmp_path,
                                                              hot_env,
                                                              monkeypatch,
                                                              capsys):
    """Fetched-but-unstored is the ONLY honest dead_at_fetch: member IS in
    res (completed=True) yet no file. Not a STARVED member — the fetcher
    returned it; the store died. STARVED line must NOT fire here."""
    real_upsert = store_io.upsert

    def upsert(ticker, region, frame, **kw):
        if ticker == "BAD":
            raise OSError("planted store death")
        return real_upsert(ticker, region, frame, **kw)

    monkeypatch.setattr(store_io, "upsert", upsert)
    plan = _plan(["OK", "BAD"])
    errors: list[str] = []
    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=lambda b, p:
                            ({t: _mk_frame() for t in b}, {}), errors=errors)
    assert facts["BAD"]["batch_completed"] is True     # frame WAS returned
    assert not (tmp_path / "BAD.csv").exists()
    assert any("BAD: store: planted store death" in e for e in errors)
    assignments, _ = gen.build_accounting(plan, _stub(["OK"]), facts)
    assert assignments["BAD"] == store_io.DEAD_AT_FETCH
    err = capsys.readouterr().err
    assert "STARVED" not in err


# ── (f) T22 (MC 10312): the typed seam revives the ladder — no raises ───────

def _multi_bulk(tickers, days=3):
    """yfinance group_by='ticker' bulk frame (uppercase MultiIndex cols —
    the shape _frames_from gates), settled w.r.t. the frozen NOW fixture."""
    idx = pd.date_range(end=NOW.date() - timedelta(days=1), periods=days)
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low",
                                                 "Close", "Volume"]])
    return pd.DataFrame(10.0, index=idx, columns=cols)


def test_typed_rate_limited_steps_the_ladder_without_raising(tmp_path,
                                                             single_batch_env):
    """THE F-A REFUTATION PLANT (c2 §1b): production NEVER RAISED, so the
    raise-scripted T18 suite proved an INERT ladder. Same script positions
    as test_ladder_steps_cool_down_and_reset_after_five_clean — but the
    events are typed answers ({} res, member rate_limited), NEVER raises:
    the SAME rungs step, COOLDOWN exactly-once, errors stay EMPTY (nothing
    crashed), and every requeued member lands in round 2 (typed then
    None-dominated)."""
    sleeps = single_batch_env
    stats: dict = {}
    rl_calls = {0, 1, 6, 12}             # same event positions as the raise pin
    plan = _plan([f"TK{i}" for i in range(13)])
    calls = []

    def fetcher(batch, period):                # production shape, never raises
        i = len(calls)
        calls.append(batch[0])
        if i in rl_calls:
            return {}, {batch[0]: "rate_limited"}   # the live 429 shape
        return {batch[0]: _mk_frame()}, {}

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher, stats=stats)
    assert sleeps == [1.0, 2.0,                            # typed events 1, 2
                      0.5, 0.5, 0.5, 0.5,                  # 4 clean: no reset
                      4.0,                                 # event 3 climbs on
                      0.5, 0.5, 0.5, 0.5, 0.5,             # 5 clean: reset
                      1.0,                                 # event back to 1 s
                      999.0,                               # ONE cooldown
                      0.5, 0.5, 0.5, 0.5]                  # re-queue paced
    assert sleeps.count(999.0) == 1
    assert len(calls) == 17                                # 13 walk + 4 round
    assert all(f["batch_completed"] for f in facts.values())
    assert stats["rl_events"] == 4 and stats["capture"] == "ctx"
    assert stats["attempted"] == stats["landed"] == 13
    assert stats["typed_counts"] == {"not_found": 0, "rate_limited": 0,
                                     "undecidable": 0}     # landed dominates


def test_typed_storm_tops_the_ladder_at_ten_seconds(tmp_path, single_batch_env,
                                                    capsys):
    """Storm-shaped (PLANT-1): EVERY call answers ({}, {member: rate_limited})
    — NOTHING raises, nothing crashes; the ladder climbs and TOPS at the 10 s
    cap in both rounds, COOLDOWN exactly once, all members honest
    batch_completed=False (rate_limited never accrues as not_fetched
    evidence elsewhere: neutral here)."""
    sleeps = single_batch_env
    stats: dict = {}
    plan = _plan([f"TK{i}" for i in range(8)])

    def fetcher(batch, period):
        return {}, {batch[0]: "rate_limited"}

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher, stats=stats)
    assert sleeps == [1.0, 2.0, 4.0, 8.0] + [10.0] * 4 + [999.0] + [10.0] * 8
    assert sleeps.count(999.0) == 1
    assert all(f["batch_completed"] is False for f in facts.values())
    assert stats["rl_events"] == 16
    assert stats["typed_counts"]["rate_limited"] == 8   # final observations
    assert "BATCH-EMPTY-OR-DEAD" in capsys.readouterr().err


def test_typed_absence_classes_never_pace_and_facts_follow_final_observation(
        tmp_path, single_batch_env):
    """not_found / undecidable are ACCRUAL evidence, not backoff evidence:
    zero rungs. facts['typed'] is final-observation-wins EXACTLY like
    batch_completed: Z's re-queue class OVERWRITES the walk class; Y's
    landing DOMINATES with None; stats count the FINAL state only."""
    sleeps = single_batch_env
    stats: dict = {}
    plan = _plan(["Y", "Z"])
    seq = [({}, {"Y": "not_found"}), ({}, {"Z": "not_found"}),
           ({"Y": _mk_frame()}, {}), ({}, {"Z": "undecidable"})]
    calls = []

    def fetcher(batch, period):
        item = seq[len(calls)]
        calls.append(batch[0])
        return item

    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, fetcher=fetcher, stats=stats)
    assert sleeps == [0.5] * 4                      # walk + round, no rungs
    assert facts["Y"] == {"batch_completed": True, "typed": None}
    assert facts["Z"] == {"batch_completed": False, "typed": "undecidable"}
    assert stats["typed_counts"] == {"not_found": 0, "rate_limited": 0,
                                     "undecidable": 1}
    assert stats["landed"] == 1 and stats["rl_events"] == 0


def test_capture_loss_falls_back_to_the_exception_arm(tmp_path, hot_env,
                                                      monkeypatch, capsys):
    """PLANT-6 (capture-lost): the guarded private import dies EXACTLY like a
    yfinance bump moves names (delattr) -> fetch_batch_typed answers via the
    PUBLIC yf.download(**same kwargs) with typed={} + stderr
    'TYPED-CAPTURE unavailable'; run_fetch's default seam records
    capture='unavailable', zero typed, and the ladder keeps ONLY the
    exception arm (no raise, no typed -> BATCH_DELAY pace only, no cooldown).
    Fail-closed: the public path still lands frames; nothing accrues blind."""
    import rocket.data.bulk_fetcher as bf
    import yfinance.multi as ym
    sleeps = hot_env                                # recorder + seam constants
    monkeypatch.delattr(ym, "_download_impl")       # the bump shape: gone
    stats: dict = {}
    seen_kwargs: list[dict] = []

    def public_download(**kw):
        seen_kwargs.append(dict(kw))
        return None if len(seen_kwargs) == 1 else _multi_bulk(["P1", "P2"])

    monkeypatch.setattr(bf.yf, "download", public_download)
    plan = _plan(["P1", "P2"])
    facts = gen.fetch_store(plan, skip_fetch=False, now_fn=lambda: NOW,
                            cache_dir=tmp_path, stats=stats)  # DEFAULT seam
    assert stats["capture"] == "unavailable"
    assert stats["typed_counts"] == {"not_found": 0, "rate_limited": 0,
                                     "undecidable": 0}
    assert stats["attempted"] == 2 and stats["landed"] == 2 and not stats["rl_events"]
    assert facts["P1"]["batch_completed"] is True and facts["P1"]["typed"] is None
    assert sleeps == [0.5, 0.5]                     # BATCH_DELAY only, once/round
    err = capsys.readouterr().err
    assert "TYPED-CAPTURE unavailable" in err       # the rot is LOUD nightly
    assert seen_kwargs[0]["group_by"] == "ticker"   # public fallback: same kwargs
    assert seen_kwargs[0]["tickers"] == ["P1", "P2"]
    assert (tmp_path / "P1.csv").exists()           # public path still stores


def test_stats_out_param_stays_untouched_under_skip_fetch(tmp_path):
    """The errors=None idiom (fetch_store.py:57-58): skip_fetch returns early
    and NEVER fills — the caller's emptiness test IS the journal gate
    (a skip night journals nothing)."""
    stats = {"keep": 1}
    facts = gen.fetch_store(_plan(["TK0"]), skip_fetch=True,
                            now_fn=lambda: NOW, cache_dir=tmp_path,
                            stats=stats)
    assert stats == {"keep": 1}
    assert facts["TK0"] == {"batch_completed": False, "typed": None}
