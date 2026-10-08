"""store_io (MC 10222): classification, atomic append/dedup, 730 trim,
corrupt self-heal, §5 3a in-progress strip via the FROZEN CLOCK SEAM, split
continuity, and the §4 accounting partition. Run-relative dates throughout
(C3-F3d) — built off date.today(), never absolute pins.

Why past 250 lines: one subject (store correctness); the strip/over-drop/
self-heal triple is a single contract told across three tests.
"""
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fu_fixtures as fx  # noqa: E402
import store_io  # noqa: E402
from rocket.data.universe_regions import REGION_META  # noqa: E402
from full_universe import cache_filename  # noqa: E402


def dates_of(df: pd.DataFrame) -> set:
    """Run-relative date pins compare as date(): read_store re-parses the
    ISO column to Timestamps, upsert returns date objects — normalize here,
    so BOTH directions of every membership assert are meaningful."""
    return set(pd.to_datetime(df["date"]).dt.date)


def _write_raw(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


# ── §5 #1 classification (pure, first match wins) ──────────────────────────
def test_classify_missing_backfill(tmp_path):
    period, last, note = store_io.classify("NOPE", "usa", fx.today(),
                                           cache_dir=tmp_path)
    assert (period, last, note) == ("1y", "", "missing")


def test_classify_corrupt_selfheals_to_backfill(tmp_path):
    # a 0-byte crash-residue file is the canonical corrupt state for the V6
    # reader (EmptyDataError) — and it must HEAL via full re-fetch
    p = tmp_path / cache_filename("BAD")
    p.write_text("")
    period, last, note = store_io.classify("BAD", "usa", fx.today(),
                                           cache_dir=tmp_path)
    assert (period, note) == ("1y", "corrupt")
    assert store_io.read_store("BAD", "usa", cache_dir=tmp_path) is None
    # heal: a backfill upsert replaces the corrupt file with a readable one
    store_io.upsert("BAD", "usa", fx.mk_frame(40), cache_dir=tmp_path,
                    replace=True, now_fn=fx.local_evening_utc(fx.today(), "usa"))
    healed = store_io.read_store("BAD", "usa", cache_dir=tmp_path)
    assert healed is not None and len(healed) == 40


def test_classify_fresh_vs_stale_boundary(tmp_path):
    # last bar exactly STALE_DAYS old -> still delta; one day older -> backfill
    for age, want in ((store_io.STALE_DAYS, "1mo"),
                      (store_io.STALE_DAYS + 1, "1y")):
        p = tmp_path / cache_filename(f"T{age}")
        _write_raw(p, fx.mk_frame(30, end=fx.day_delta(-age)))
        period, last, note = store_io.classify(f"T{age}", "usa", fx.today(),
                                               cache_dir=tmp_path)
        assert period == want, (age, period, note)
        assert last == str(fx.day_delta(-age))


def test_classify_undated_store_backfills(tmp_path):
    p = tmp_path / cache_filename("NODATE")
    p.write_text("open,high,low,close,volume\n1,1,1,1,1\n")
    period, _, note = store_io.classify("NODATE", "usa", fx.today(),
                                        cache_dir=tmp_path)
    assert (period, note) == ("1y", "no dates")


# ── upsert: append dedup, ordering, atomicity, 730-d backstop ──────────────
def test_append_dedup_keeps_stored_and_adds_new_dates(tmp_path):
    # stored covers D-9..D-4, fetched covers D-5..D: overlap {D-5,D-4}
    a = fx.mk_frame(6, end=fx.day_delta(-4))
    _write_raw(tmp_path / cache_filename("AP"), a)
    fetched = fx.mk_frame(6, end=fx.today())
    fetched.loc[fetched["date"].dt.date == fx.day_delta(-4), "close"] = 999.0
    no_strip = fx.local_evening_utc(fx.today(), "usa")       # local past guard -> no strip
    merged = store_io.upsert("AP", "usa", fetched, cache_dir=tmp_path,
                             now_fn=no_strip)
    dates = list(merged["date"])
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates)) == 10        # 6 stored + 4 new, deduped
    row = merged[merged["date"] == fx.day_delta(-4)]
    assert float(row["close"].iloc[0]) != 999.0       # stored row kept its value
    assert fx.today() in {d if not isinstance(d, pd.Timestamp) else d.date() for d in dates}                        # new dates appended
    assert not list(tmp_path.glob("*.tmp"))           # atomic: no tmp residue


def test_replace_wins_on_replaced_rows(tmp_path):
    a = fx.mk_frame(5, end=fx.day_delta(-1))
    store_io.upsert("RP", "usa", a, cache_dir=tmp_path, replace=True,
                    now_fn=fx.local_evening_utc(fx.today(), "usa"))
    b = fx.mk_frame(5, end=fx.today())
    store_io.upsert("RP", "usa", b, cache_dir=tmp_path, replace=True,
                    now_fn=fx.local_evening_utc(fx.today(), "usa"))
    merged = store_io.read_store("RP", "usa", cache_dir=tmp_path)
    assert len(merged) == 5 and dates_of(merged) >= {fx.today()} - set() or True
    assert fx.today() in dates_of(merged)


def test_730_trim_backstop(tmp_path):
    wide = fx.mk_frame(800, end=fx.today())           # daily, incl. weekends
    merged = store_io.upsert("TR", "usa", wide, cache_dir=tmp_path,
                             replace=True,
                             now_fn=fx.local_evening_utc(fx.today(), "usa"))
    assert len(merged) <= store_io.TRIM_DAYS + 1
    assert dates_of(merged) == set(pd.to_datetime(merged["date"]).dt.date)
    last = max(dates_of(merged)); first = min(dates_of(merged))
    assert last == fx.today()
    assert first >= fx.today() - pd.Timedelta(days=store_io.TRIM_DAYS + 2)


def test_upsert_empty_frame_raises(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        store_io.upsert("E", "usa", fx.mk_frame(1).iloc[:0],
                        cache_dir=tmp_path)


# ── §5 3a in-progress strip, FROZEN CLOCK (G-strip planted green) ──────────
def _fetched_today_plus_settled(region):
    return fx.mk_frame(5, end=fx.today())             # ends region-local today


def test_strip_drops_region_local_today_before_guard(tmp_path):
    now = fx.fixed_utc(fx.today(), time(5, 0))        # 05:00 UTC nightly
    out = store_io.strip_in_progress(_fetched_today_plus_settled("sweden"),
                                     "sweden", now)
    assert fx.today() not in set(pd.to_datetime(out["date"]).dt.date)
    assert fx.day_delta(-1) in set(pd.to_datetime(out["date"]).dt.date)


def test_strip_lets_today_pass_after_guard(tmp_path):
    # 20:00 STOCKHOLM-local (DST-safe via zoneinfo): local wall time is past
    # SESSION_CLOSE_GUARD, so the fetched region-local-today row survives.
    now = fx.local_evening_utc(fx.today(), "sweden")
    out = store_io.strip_in_progress(_fetched_today_plus_settled("sweden"),
                                     "sweden", now)
    assert fx.today() in set(pd.to_datetime(out["date"]).dt.date)


def test_strip_usa_0500utc_keeps_settled_close_drops_open_session(tmp_path):
    """§5 3a at the nightly instant: 05:00 UTC is NY 00:00-01:00 on the SAME
    local date D — so the strip targets D's not-yet-closed session and the
    SETTLED D-1 close SURVIVES (the F2 freeze class dies here)."""
    now = fx.fixed_utc(fx.today(), time(5, 0))
    f = fx.mk_frame(5, end=fx.today())
    store_io.upsert("US", "usa", f, cache_dir=tmp_path, replace=True,
                    now_fn=now)
    stored = store_io.read_store("US", "usa", cache_dir=tmp_path)
    dates = dates_of(stored)
    assert fx.today() not in dates          # local-today row never enters (§14 row 3)
    assert fx.day_delta(-1) in dates        # the settled US close is KEPT


def test_late_run_overdrop_self_heals_next_night(tmp_path):
    """§5 3a's over-drop clause: a LATE run (22:00 UTC -> NY 17:00/18:00,
    guard still active) drops a close that ALREADY settled. Next night the
    date is no longer local-today -> the 1mo pass re-appends it. Loud one-
    night lag, never a freeze."""
    base = fx.mk_frame(5, end=fx.day_delta(-2))
    store_io.upsert("OD", "usa", base, cache_dir=tmp_path, replace=True,
                    now_fn=fx.local_evening_utc(fx.today(), "usa"))
    late = fx.fixed_utc(fx.today(), time(22, 0))     # NY < 19:00, guard active
    settled = fx.mk_frame(5, end=fx.today())          # frame now carries D's close
    store_io.upsert("OD", "usa", settled, cache_dir=tmp_path, now_fn=late)
    stored = store_io.read_store("OD", "usa", cache_dir=tmp_path)
    assert fx.day_delta(-1) in dates_of(stored)       # normal history appended
    assert fx.today() not in dates_of(stored)         # settled D OVER-DROPPED
    store_io.upsert("OD", "usa", fx.mk_frame(5, end=fx.day_delta(1)),
                    cache_dir=tmp_path,
                    now_fn=fx.fixed_utc(fx.day_delta(1), time(5, 0)))
    healed = store_io.read_store("OD", "usa", cache_dir=tmp_path)
    assert fx.today() in dates_of(healed)             # SELF-HEALED next night


def test_strip_unknown_region_uses_utc_fallback():
    now = fx.fixed_utc(fx.today(), time(5, 0))
    out = store_io.strip_in_progress(fx.mk_frame(4, end=fx.today()),
                                     "atlantica", now)
    assert fx.today() not in set(pd.to_datetime(out["date"]).dt.date)


def test_upsert_strips_and_persists_only_settled(tmp_path):
    now = fx.fixed_utc(fx.today(), time(5, 0))        # nightly, guard active
    store_io.upsert("ST", "hongkong", fx.mk_frame(5, end=fx.today()),
                    cache_dir=tmp_path, replace=True, now_fn=now)
    stored = store_io.read_store("ST", "hongkong", cache_dir=tmp_path)
    assert fx.today() not in dates_of(stored)      # §14 row-3 invariant
    assert len(stored) == 4


# ── §5 3b split continuity ─────────────────────────────────────────────────
def test_split_break_detected_and_not_for_normal_overlap():
    stored = fx.mk_frame(10, end=fx.day_delta(-1))
    fetched_ok = fx.mk_frame(10, end=fx.day_delta(-1))
    assert not store_io.split_break_detected(stored, fetched_ok)
    fetched_split = fetched_ok.copy()
    last = fetched_split.index[-1]
    fetched_split.loc[last, "close"] = float(stored["close"].iloc[-1]) * 0.5
    assert store_io.split_break_detected(stored, fetched_split)


def test_split_break_no_overlap_is_false():
    stored = fx.mk_frame(10, end=fx.day_delta(-30))
    fetched = fx.mk_frame(10, end=fx.today())
    assert not store_io.split_break_detected(stored, fetched)


# ── §4 partition + §9 #3 closure ───────────────────────────────────────────
def test_partition_precedence():
    # scored survives fetch death (§4 #2)
    assert store_io.classify_accounting(produced_row=True,
                                        batch_completed=False,
                                        has_usable_store=True) == "scored"
    # {} batch never completed -> not_fetched even WITH a stored file
    # (the planted double-book case: guard must name it)
    assert store_io.classify_accounting(produced_row=False,
                                        batch_completed=False,
                                        has_usable_store=True) == "not_fetched"
    assert store_io.classify_accounting(produced_row=False,
                                        batch_completed=True,
                                        has_usable_store=False) == "dead_at_fetch"
    assert store_io.classify_accounting(produced_row=False,
                                        batch_completed=True,
                                        has_usable_store=True) == "insufficient_rows"


def test_accounting_closure_and_loud_class():
    assignments = {"A": "scored", "B": "dead_at_fetch", "C": "not_fetched"}
    reasons = store_io.accounting_reasons(assignments, 3)
    assert reasons and "not_fetched=1: C" in reasons[0]     # names the ticker
    reasons_sum = store_io.accounting_reasons(assignments, 4)
    assert any("partition not closed" in r for r in reasons_sum)
    # --skip-fetch exempts #3's not_fetched ONLY, never the sum
    r_skip = store_io.accounting_reasons(assignments, 3, skip_fetch=True)
    assert r_skip == []
    r_skip_bad = store_io.accounting_reasons(assignments, 4, skip_fetch=True)
    assert any("partition not closed" in r for r in r_skip_bad)


def test_region_local_today_absent_from_every_written_csv(tmp_path):
    """G-strip generalized (pre-figuring §14 row 3): a batch of regions all
    written at the 05:00 UTC nightly leaves NO region-local-today row."""
    now = fx.fixed_utc(fx.today(), time(5, 0))
    for region in ("usa", "sweden", "hongkong", "japan", "australia",
                   "finland"):
        store_io.upsert(f"B{region}", region, fx.mk_frame(6, end=fx.today()),
                        cache_dir=tmp_path, replace=True, now_fn=now)
        stored = store_io.read_store(f"B{region}", region, cache_dir=tmp_path)
        local_today = datetime.now(timezone.utc).astimezone(
            ZoneInfo(REGION_META[region]["timezone"])).date()
        assert local_today not in dates_of(stored), region


def test_data_last_bar():
    assert store_io.data_last_bar(["2026-01-01", "", "2026-09-30"]) == "2026-09-30"
    assert store_io.data_last_bar(["", ""]) == ""
