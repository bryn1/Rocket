"""Per-ticker CSV store: classification, atomic writes, bar correctness (MC 10222).

The ONLY writer of ``source/data/raw/`` (DESIGN §4 store contract). One CSV per
unique ticker, V6 shape (ISO ``date`` column + lowercase ohlcv); the tolerant
reader is the V6 ``_read_cache`` ported from generate_demo_page.py (that file
is T4's; it is NOT touched here).

Correctness steps at write time (DESIGN §5):
  3a  in-progress strip: drop fetched rows dated region-local "today" while
      local wall time < SESSION_CLOSE_GUARD (a frozen clock seam drives
      tests). Over-drops (a settled US close at 05:00 UTC) self-heal: the
      stripped date < next night's window and re-appends settled (§5 3a).
  3b  split continuity: a delta whose fetched close at the stored last date
      deviates > split_adjust.JUMP_THRESHOLD => re-queue as backfill (the
      caller re-fetches 1y and upserts with replace=True).

Accounting (§4, C2-F2 partition): ``classify_accounting`` is the pure
first-match-wins classifier; ``accounting_reasons`` re-checks closure and
not_fetched == 0 (§9 #3) and names tickers in the loud class.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_SOURCE = Path(__file__).resolve().parents[1]
for _p in (str(_SOURCE), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from full_universe import CACHE_DIR, cache_filename  # noqa: E402
from rocket.data.universe_regions import REGION_META  # noqa: E402
from rocket.dataquality.split_adjust import JUMP_THRESHOLD  # noqa: E402

STALE_DAYS = 28            # last bar older than this => backfill (DESIGN §5 #1)
TRIM_DAYS = 730            # kept as a backstop; cannot fire while period=1y
MIN_SCORE_ROWS = 20        # §4 class 2/3 bound (kept from the 35-ticker era)
SESSION_CLOSE_GUARD = time(19, 0)  # local; sits above the latest cash close
                                   # (Helsinki ~18:25-18:30, INFERRED — C2-F6.2)
PERIOD_BACKFILL, PERIOD_DELTA = "1y", "1mo"

# classes (§4 partition, precedence order — first match wins)
NOT_FETCHED, SCORED, INSUFFICIENT_ROWS, DEAD_AT_FETCH = (
    "not_fetched", "scored", "insufficient_rows", "dead_at_fetch")

_OHLCV = ("open", "high", "low", "close", "volume")


def utc_now() -> datetime:
    """Clock seam default (ISO UTC); tests inject a frozen callable."""
    return datetime.now(timezone.utc)


def read_cache(csv_path: Path) -> pd.DataFrame:
    """V6 tolerant reader (moved from generate_demo_page._read_cache): new
    caches carry a date column; old ones stay loadable."""
    cols = pd.read_csv(csv_path, nrows=0).columns
    if "date" in cols:
        return pd.read_csv(csv_path, parse_dates=["date"])
    return pd.read_csv(csv_path)


def last_bar_date(df: pd.DataFrame) -> str:
    """ISO date of the newest bar; '' when the frame carries no dates."""
    if df is None or len(df) == 0:
        return ""
    if "date" in df.columns:
        return str(pd.to_datetime(df["date"]).iloc[-1].date())
    if isinstance(df.index, pd.DatetimeIndex) or df.index.name == "date":
        return str(pd.DatetimeIndex(df.index)[-1].date())
    return ""


def usable_bars(df: pd.DataFrame) -> int:
    return 0 if df is None else len(df)


def read_store(ticker: str, region: str, *,
               cache_dir: Path | None = None) -> pd.DataFrame | None:
    """Stored frame or None (missing/corrupt -> None: self-heal via backfill)."""
    path = (cache_dir or CACHE_DIR) / cache_filename(ticker)
    if not path.exists():
        return None
    try:
        return read_cache(path)
    except Exception:  # noqa: BLE001 — corrupt CSV heals by re-fetch (§4)
        return None


def classify(ticker: str, region: str, today: date, *,
             cache_dir: Path | None = None
             ) -> tuple[str, str, str]:
    """Pure §5 #1 classifier: (period, last_bar, note).

    missing | corrupt | last bar > STALE_DAYS | no usable date  -> backfill;
    otherwise delta. first-match-wins, one class per ticker per run.
    """
    path = (cache_dir or CACHE_DIR) / cache_filename(ticker)
    if not path.exists():
        return PERIOD_BACKFILL, "", "missing"
    try:
        stored = read_cache(path)
    except Exception:  # noqa: BLE001 — corrupt -> full re-fetch heals
        return PERIOD_BACKFILL, "", "corrupt"
    last = last_bar_date(stored)
    if not last:
        return PERIOD_BACKFILL, "", "no dates"
    age = (today - date.fromisoformat(last)).days
    if age > STALE_DAYS:
        return PERIOD_BACKFILL, last, f"stale {age}d"
    return PERIOD_DELTA, last, ""


def split_break_detected(stored: pd.DataFrame, fetched: pd.DataFrame) -> bool:
    """§5 3b: close deviation at the stored last date > JUMP_THRESHOLD.
    Fetched frame lacking that date -> False (the overlap window says so)."""
    try:
        if stored is None or fetched is None or stored.empty:
            return False
        d = pd.to_datetime(stored["date"]).iloc[-1]
        s_close = float(pd.to_numeric(stored["close"]).iloc[-1])
        f = fetched.copy()
        f["date"] = pd.to_datetime(f["date"])
        row = f[f["date"] == d]
        if row.empty or s_close == 0:
            return False
        f_close = float(pd.to_numeric(row["close"]).iloc[-1])
        return abs(f_close - s_close) / abs(s_close) > JUMP_THRESHOLD
    except Exception:  # noqa: BLE001 — undecidable -> let delta stand (loud via #6)
        return False


def strip_in_progress(df: pd.DataFrame, region: str,
                      now_fn=utc_now) -> pd.DataFrame:
    """§5 3a: drop rows dated region-local today while local time <
    SESSION_CLOSE_GUARD. Unknown region key -> UTC fallback (descriptor, not
    a market fact — conservative: strip today, never a foreign date)."""
    if df is None or len(df) == 0:
        return df
    if "date" not in df.columns:
        return df  # undated frame carries no in-progress bar to drop
    tz_name = REGION_META.get(region, {}).get("timezone") or "UTC"
    now_local = now_fn().astimezone(ZoneInfo(tz_name))
    if now_local.time() >= SESSION_CLOSE_GUARD:
        return df
    dates = pd.to_datetime(df["date"])
    return df[dates.dt.date != now_local.date()].reset_index(drop=True)


def upsert(ticker: str, region: str, fetched: pd.DataFrame, *,
           cache_dir: Path | None = None, replace: bool = False,
           now_fn=utc_now) -> pd.DataFrame:
    """Store one fetched frame (the ONLY store writer path).

    Backfill class -> replace=True (full 1y frame replaces the file; a corrupt
    file is thus healed). Delta class -> append with dedup by date (stored
    rows keep theirs; only new dates are added), sort, 730-d trim backstop,
    atomic tmp+rename. In-progress strip runs on the FETCHED frame only —
    stored settled bars are never re-judged.
    """
    if fetched is None or len(fetched) == 0:
        raise ValueError(f"upsert {ticker}: empty fetched frame")
    clean = strip_in_progress(fetched.reset_index() if "date" not in fetched
                              and fetched.index.name == "date" else fetched,
                              region, now_fn)
    if clean.empty:
        raise ValueError(f"upsert {ticker}: nothing settled in fetched frame")
    clean = _normalized(clean)
    if replace:
        merged = clean
    else:
        stored = read_store(ticker, region, cache_dir=cache_dir)
        if stored is None or (isinstance(stored, pd.DataFrame) and stored.empty):
            merged = clean
        else:
            stored = _normalized(stored)
            keep_dates = set(stored["date"])
            fresh = clean[~clean["date"].isin(keep_dates)]
            merged = pd.concat([stored, fresh], ignore_index=True)
    merged = merged.sort_values("date").reset_index(drop=True)
    merged = merged[merged["date"] >= merged["date"].iloc[-1]
                    - pd.Timedelta(days=TRIM_DAYS)]
    _atomic_write(merged, (cache_dir or CACHE_DIR) / cache_filename(ticker))
    return merged


def _normalized(df: pd.DataFrame) -> pd.DataFrame:
    out = df.reset_index() if "date" not in df.columns else df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    for col in _OHLCV:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["close"]).reset_index(drop=True)


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)  # rename is atomic within one filesystem


def data_last_bar(last_dates) -> str:
    """Max ISO date over stored per-ticker last-bar dates ('' ignored)."""
    dates = [d for d in last_dates if d]
    return max(dates) if dates else ""


def classify_accounting(*, produced_row: bool, batch_completed: bool,
                        has_usable_store: bool) -> str:
    """§4 partition, first match wins (exclusive by construction, C2-F2).

    has_usable_store = the ticker's stored CSV exists AND parses (read_store
    returned a frame) — bars < MIN_SCORE_ROWS is then §4 #3's content. An
    exception while scoring a usable frame yields no row and is owned by
    insufficient_rows (identity must close; the error string rides the
    manifest).
    """
    if produced_row:
        return SCORED                # §4 #2 — tonight's fetch death is moot
    if not batch_completed:
        return NOT_FETCHED           # §4 #1 — {} after one re-queue
    if not has_usable_store:
        return DEAD_AT_FETCH         # §4 #4 — completed batch, no file at all
    return INSUFFICIENT_ROWS         # §4 #3 thin file, or scoring exception


def accounting_reasons(assignments: dict[str, str], m_unique: int, *,
                       skip_fetch: bool = False) -> list[str]:
    """§9 #3: closed identity + not_fetched == 0 (exempt under --skip-fetch
    only). Names the loud-class tickers so the guard is triage-ready."""
    reasons: list[str] = []
    total = len(assignments)
    if total != m_unique:
        reasons.append(f"partition not closed: {total} assigned != "
                       f"m_unique {m_unique}")
    nf = sorted(t for t, cls in assignments.items() if cls == NOT_FETCHED)
    if nf and not skip_fetch:
        shown = ", ".join(nf[:10]) + (f" (+{len(nf) - 10} more)" if len(nf) > 10
                                      else "")
        reasons.append(f"not_fetched={len(nf)}: {shown}")
    return reasons
