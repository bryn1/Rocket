#!/usr/bin/env python3
"""Generate the daily demo page (index.html) — generator v2 (MC 3874).

Scores via the Dash app path on Title-case frames (seam C1, V4: all 34
indicators run), persists OHLCV caches with their date column (V6), runs
the per-indicator backtest IN-PROCESS and carries the previously committed
indicator_stats.json forward if it fails (§5.3). Publish is fail-closed:
nothing is written unless _guard passes (§5.4). --dry-run writes neither
root file and renders into REPO_ROOT/.tmp/ (§6). Setting
ROCKET_FORCE_EMPTY_UNIVERSE=1 empties the universe (deterministic guard
red case for tests and smoke).

Usage: python3 scripts/generate_demo_page.py [--dry-run] [--skip-fetch]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "source"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_render  # noqa: E402
from demo_universe import CACHE_DIR, UNIVERSE, cache_filename  # noqa: E402
from rocket.data.fetcher import fetch_ohlcv  # noqa: E402

INDEX_PATH = REPO_ROOT / "index.html"
STATS_PATH = REPO_ROOT / "indicator_stats.json"
DRY_DIR = REPO_ROOT / ".tmp"
MIN_ROWS = 25        # fail-closed floor, of 35 universe tickers (§5.4)
SCHEMA_VERSION = 1   # seam C2/C3: unknown schema == absent (§5.3)


def _read_cache(csv_path: Path) -> pd.DataFrame:
    """V6 fix: new caches carry a date column; old ones stay loadable."""
    cols = pd.read_csv(csv_path, nrows=0).columns
    if "date" in cols:
        return pd.read_csv(csv_path, parse_dates=["date"])
    print(f"warning: {csv_path.name} cache has no date column (legacy shape)",
          file=sys.stderr)
    return pd.read_csv(csv_path)


def _write_cache(df: pd.DataFrame, csv_path: Path) -> None:
    """V6 fix: fetcher returns date as index (index.name='date'); persist it."""
    if df.index.name == "date" or isinstance(df.index, pd.DatetimeIndex):
        df.reset_index().to_csv(csv_path, index=False)
    else:
        df.to_csv(csv_path, index=False)


def _load_frame(ticker: str, skip_fetch: bool) -> pd.DataFrame | None:
    csv_path = CACHE_DIR / cache_filename(ticker)
    if skip_fetch and csv_path.exists():
        return _read_cache(csv_path)
    fetched = fetch_ohlcv([ticker], period="1y")
    df = fetched.get(ticker)
    if df is not None and len(df) > 10:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _write_cache(df, csv_path)
    return df


def _last_bar(df: pd.DataFrame) -> str:
    """ISO date of the newest bar, '' when the frame carries no dates."""
    if "date" in df.columns:
        return str(pd.to_datetime(df["date"]).iloc[-1].date())
    if isinstance(df.index, pd.DatetimeIndex) or df.index.name == "date":
        return str(pd.DatetimeIndex(df.index)[-1].date())
    return ""


def _score_df(ticker: str, df: pd.DataFrame, region: str) -> dict | None:
    """Score via the app's path on a Title-case frame (C1, V4: all 34 run)."""
    from rocket.backtest.indicator_eval import to_indicator_frame
    from app import _compute_all_indicators, _score_from_summary
    idf = to_indicator_frame(df)
    summary, _ = _compute_all_indicators(idf)
    rs = _score_from_summary(summary, ticker=ticker,
                             region=region)["rocket_score"]
    signal = getattr(summary, "signal", None) or (
        "BUY" if summary.buy_count > summary.sell_count else
        "SELL" if summary.sell_count > summary.buy_count else "HOLD")
    return {
        "ticker": ticker, "signal": signal, "overall": rs.overall_score,
        "momentum": rs.momentum_score, "trend": rs.trend_score,
        "volatility": rs.volatility_score, "volume": rs.volume_score,
        "close": round(float(idf["Close"].iloc[-1]), 2),
    }


def process_ticker(ticker, region, skip_fetch):
    df = _load_frame(ticker, skip_fetch)
    if df is None or len(df) < 20:
        return None, None, f"{ticker}: no data"
    return _score_df(ticker, df, region), df, None


def run_backtest(frames: dict, universe: dict) -> dict:
    """Seam C1, lazy: the import resolves only at nightly run time."""
    from rocket.backtest.indicator_eval import run_indicator_eval
    doc = run_indicator_eval(frames, universe)
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        got = doc.get("schema_version") if isinstance(doc, dict) else type(doc)
        raise ValueError(f"backtest artifact schema {got!r} != {SCHEMA_VERSION}")
    return doc


def _load_carried_stats() -> tuple[dict | None, str | None]:
    """§5.3 carry-forward: the committed file stays byte-identical, embedded."""
    if not STATS_PATH.exists():
        return None, None
    try:
        doc = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, f"{demo_render.EMPTY_BACKTEST_TEXT} (tidigare fil oläsbar: {exc})"
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        got = doc.get("generated_at") if isinstance(doc, dict) else None
        return None, (f"{demo_render.EMPTY_BACKTEST_TEXT} "
                      f"(tidigare fil från {got or '?'} har okänd schema)")
    ga = doc.get("generated_at", "?")
    return doc, (f"Backtest från {ga} bars vidare — denna löpnings backtest "
                 "lyckades inte.")


def _guard(html: str, total: int, universe: dict) -> list[str]:
    """Fail-closed §5.4: rows floor AND every tab/banner marker in the HTML."""
    reasons = []
    if total < MIN_ROWS:
        reasons.append(f"endast {total} scorerade rader (golv {MIN_ROWS})")
    for marker in ([demo_render.BANNER_MARKER, demo_render.INDICATOR_TAB_MARKER]
                   + [f'id="panel-{r}"' for r in universe]):
        if marker not in html:
            reasons.append(f"marker saknas: {marker}")
    return reasons


def publish() -> None:
    # Sync with the mirror first (the reconciler may have moved it); unstaged
    # changes would block the rebase — stash them around it.
    subprocess.run(["git", "stash", "push", "--include-untracked=no", "-m",
                    "demo-publish auto-stash"], cwd=REPO_ROOT, check=False)
    subprocess.run(["git", "pull", "--rebase", "bryn1", "main"],
                   cwd=REPO_ROOT, check=False)
    subprocess.run(["git", "stash", "pop"], cwd=REPO_ROOT, check=False)
    subprocess.run(["git", "add", "index.html", "indicator_stats.json"],
                   cwd=REPO_ROOT, check=True)  # seam C5: exactly these two
    subprocess.run(
        ["git", "-c", "user.name=code (MC 3874)", "-c",
         "user.email=code@agent-town.local", "commit", "-m",
         "publish: regenerate demo page + indicator stats (MC 3874)"],
        cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "bryn1", "HEAD:main"], cwd=REPO_ROOT,
                   check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="render to .tmp/, write nothing in the root, no git")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="reuse cached CSVs")
    args = parser.parse_args()

    universe = {} if os.environ.get("ROCKET_FORCE_EMPTY_UNIVERSE") == "1" \
        else UNIVERSE  # the empty case is the deterministic guard red case
    results, errors, frames = {}, [], {}
    for region, tickers in universe.items():
        rows = []
        for ticker in tickers:
            try:
                row, df, err = process_ticker(ticker, region, args.skip_fetch)
                if err:
                    errors.append(err)
                if row:
                    rows.append(row)
                if df is not None:
                    frames[ticker] = df
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the page
                errors.append(f"{ticker}: {exc}")
        results[region] = rows

    now = datetime.now(timezone.utc)
    run_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_ts = now.strftime("%Y-%m-%d %H:%M UTC")
    bars = [_last_bar(df) for df in frames.values()]
    data_last_bar = max(b for b in bars if b) if any(bars) else ""

    fresh, doc, note = None, None, None
    try:
        doc = fresh = run_backtest(frames, universe)
    except Exception as exc:  # noqa: BLE001 — backtest never blocks scoring
        errors.append(f"backtest: {exc} (carry-forward)")
        doc, note = _load_carried_stats()

    html = demo_render.render(results, run_ts, data_last_bar, doc, note)
    total = sum(len(rows) for rows in results.values())
    reasons = _guard(html, total, universe)
    if reasons:
        print(f"FAIL-CLOSED — ingenting skrivet: {'; '.join(reasons)}",
              file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        DRY_DIR.mkdir(exist_ok=True)
        (DRY_DIR / "index.html").write_text(html, encoding="utf-8")
        if fresh is not None:
            (DRY_DIR / "indicator_stats.json").write_text(
                json.dumps(fresh, indent=2), encoding="utf-8")
        print(f"Dry run — rendered {DRY_DIR / 'index.html'}: {total} tickers, "
              f"{len(errors)} notes: {errors}")
    else:
        INDEX_PATH.write_text(html, encoding="utf-8")
        if fresh is not None:
            STATS_PATH.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        publish()
        print(f"Published to bryn1 main: {total} tickers, "
              f"{len(errors)} notes: {errors}")


if __name__ == "__main__":
    main()
