"""Bulk OHLCV fetcher — downloads 25k+ tickers in parallel with checkpoint.

Usage:
    python3 -m rocket.data.bulk_fetcher --period 10y
    python3 -m rocket.data.bulk_fetcher --period 20y --regions usa sweden
    python3 -m rocket.data.bulk_fetcher --resume  # continue from checkpoint
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent / "data"
OHLCV_DIR = BASE_DIR / "ohlcv"
CHECKPOINT_FILE = BASE_DIR / "ohlcv" / "fetch_checkpoint.json"
UNIVERSE_CACHE = Path(__file__).resolve().parent / "universe_cache.json"

BATCH_SIZE = 50       # tickers per yf.download() call
MAX_WORKERS = 20      # parallel download threads
BATCH_DELAY = 0.5     # seconds between batches (rate limiting)
MAX_ROWS_PER_TICKER = 5000  # ~20 years of daily data
MIN_ROWS_FOR_10Y = 2200   # minimum rows to qualify as 10+ years


# ── Load universe ──────────────────────────────────────────────────────────

def load_universe(regions: Optional[List[str]] = None) -> List[str]:
    """Load tickers from universe cache, optionally filtered by regions."""
    with open(UNIVERSE_CACHE) as f:
        data = json.load(f)

    tickers = data.get("tickers", {})
    result = []

    if regions:
        for region in regions:
            if region in tickers:
                result.extend(tickers[region])
    else:
        for region, ticker_list in tickers.items():
            result.extend(ticker_list)

    # Deduplicate
    return list(set(result))


# ── Checkpoint management ─────────────────────────────────────────────────

def load_checkpoint() -> Dict:
    """Load or create checkpoint state."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"completed": {}, "failed": {}, "started_at": None, "last_updated": None}


def save_checkpoint(state: Dict) -> None:
    """Save checkpoint state to disk."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(CHECKPOINT_FILE.parent, exist_ok=True)
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)


# ── Batch fetching ─────────────────────────────────────────────────────────

def _fetch_batch(tickers_batch: List[str], period: str) -> Dict[str, pd.DataFrame]:
    """Download OHLCV for a batch of tickers in a SINGLE yf.download() call.
    
    Returns: ticker -> DataFrame for successfully fetched tickers only.
    """
    results = {}

    for attempt in range(3):
        try:
            df = yf.download(
                tickers=tickers_batch,
                period=period,
                interval="1d",
                progress=False,
                threads=True,
                timeout=30,
                group_by="ticker"  # easier to split by ticker
            )

            if df is None or df.empty:
                logger.warning(f"Batch {tickers_batch[:3]}: empty response")
                return {}

            # yfinance returns MultiIndex columns when group_by="ticker"
            # Columns: (ticker, open), (ticker, high), ...
            # Or single Index when only one ticker
            if isinstance(df.columns, pd.MultiIndex):
                tickers_in_df = df.columns.get_level_values(0).unique()
                for ticker in tickers_in_df:
                    ticker_df = df[ticker].dropna()
                    # Ensure required columns
                    required = ['Open', 'High', 'Low', 'Close', 'Volume']
                    if all(col in ticker_df.columns for col in required):
                        ticker_df = ticker_df[required].copy()
                        ticker_df.columns = ['open', 'high', 'low', 'close', 'volume']  # type: ignore
                        ticker_df.index.name = 'date'  # type: ignore
                        if len(ticker_df) > 0:
                            results[ticker] = ticker_df

            return results

        except Exception:
            logger.warning(f"Batch fetch attempt {attempt+1} failed")
            time.sleep(2 * (attempt + 1))

    return {}


# ── Typed capture seam (MC 10312, DEADLIST-DESIGN-c2 §1b) ────────────────────
# This module is the ONLY yfinance-error reader; fetch_store's ladder is the
# ONE backoff owner — nothing here sleeps or retries, one attempt per call.

def _typed_import():
    """Guarded import of the PRIVATE yfinance capture seam: returns
    (_download_impl, _DownloadCtx), or None when capture is lost (a bump that
    renames/moves them -> public fallback, design's canary). One import home
    for both fetch_batch_typed and typed_capture_state."""
    try:
        from yfinance.multi import _download_impl, _DownloadCtx
    except (ImportError, AttributeError, TypeError):
        return None
    return _download_impl, _DownloadCtx


def typed_capture_state() -> str:
    """'ctx' while the private capture seam resolves, else 'unavailable' —
    c2 §5 canary: private-name drift must surface in the nightly journal,
    never silently stay inert."""
    return "ctx" if _typed_import() is not None else "unavailable"


def _classify_typed(err: str) -> str:
    """DEADLIST-DESIGN-c2 §1b — the ONE classification home. `err` is the
    stored STRING, class-prefix-STRIPPED by yfinance (VERIFIED production
    fact), so the tests are message-conjunctions; 'possibly delisted' and
    'No data found' must BOTH be in err (DA-P2-d — a bare `and` of the two
    tests would let either alone classify not_found). NEVER defaults to
    not_found: fail-closed toward non-accrual."""
    if ("YFPricesMissingError" in err or "YFTickerMissingError" in err
            or ("possibly delisted" in err and "No data found" in err)):
        return "not_found"
    if ("YFRateLimitError" in err or "too many requests" in err.lower()
            or "rate limited" in err.lower()):
        return "rate_limited"
    return "undecidable"


def _frames_from(df):
    """Frame gate for the typed seam: identical dropna/required-cols semantics
    as legacy _fetch_batch — deliberately duplicated, because _fetch_batch is
    pinned byte-identical (fetch_bulk's parquet contract)."""
    results: Dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return results
    if isinstance(df.columns, pd.MultiIndex):
        for ticker in df.columns.get_level_values(0).unique():
            ticker_df = df[ticker].dropna()
            required = ['Open', 'High', 'Low', 'Close', 'Volume']
            if all(col in ticker_df.columns for col in required):
                ticker_df = ticker_df[required].copy()
                ticker_df.columns = ['open', 'high', 'low', 'close', 'volume']  # type: ignore
                ticker_df.index.name = 'date'  # type: ignore
                if len(ticker_df) > 0:
                    results[ticker] = ticker_df
    return results


def fetch_batch_typed(batch: List[str],
                      period: str) -> Tuple[Dict[str, pd.DataFrame],
                                            Dict[str, str]]:
    """(results, typed) per c2 §1b. Guarded private import; capture lost ->
    public yf.download(**same kwargs) with typed={} and stderr
    'TYPED-CAPTURE unavailable'. typed maps every REQUESTED member ABSENT
    from results to its class (members IN results carry NO entry), classified
    by _classify_typed on ctx.errors strings — an absent member with no
    recorded error is 'undecidable' (silent loss is not evidence)."""
    imp = _typed_import()
    if imp is None:
        print("TYPED-CAPTURE unavailable", file=sys.stderr)
        df = yf.download(
            tickers=batch, period=period, interval="1d", progress=False,
            threads=True, timeout=30, group_by="ticker"
        )
        return _frames_from(df), {}
    _download_impl, _DownloadCtx = imp
    ctx = _DownloadCtx()
    df = _download_impl(
        ctx, batch, period=period, interval="1d", progress=False,
        threads=True, timeout=30, group_by="ticker"
    )
    results = _frames_from(df)
    typed = {t: _classify_typed(str(ctx.errors.get(t, "")))
             for t in batch if t not in results}
    return results, typed


def _validate_ticker(ticker: str, period: str = "5y") -> Optional[pd.DataFrame]:
    """Quick validation: does this ticker actually have data?"""
    return None  # Skip per-ticker validation — batch fetcher handles failures


# ── Storage ─────────────────────────────────────────────────────────────────

def save_parquet(ticker: str, df: pd.DataFrame) -> None:
    """Save OHLCV data to parquet, partitioned by year."""
    # Split by year
    df = df.copy()
    df = df.reset_index()
    df["year"] = df["date"].dt.year  # type: ignore

    for year, year_df in df.groupby("year"):
        year_df = year_df.drop(columns=["year"])
        year_df = year_df[["open", "high", "low", "close", "volume"]]

        ticker_dir = OHLCV_DIR / ticker
        os.makedirs(ticker_dir, exist_ok=True)

        filepath = ticker_dir / f"{year}.parquet"
        year_df.to_parquet(filepath, engine="pyarrow", compression="snappy")


def load_existing_count() -> Dict:
    """Load list of already-downloaded (ticker, year) pairs."""
    existing = {"ok": set(), "incomplete": set()}

    if not OHLCV_DIR.exists():
        return existing

    for ticker_dir in OHLCV_DIR.iterdir():
        if ticker_dir.is_dir():
            ticker = ticker_dir.name
            years = set()
            for p in ticker_dir.glob("*.parquet"):
                years.add(int(p.stem))

            if len(years) >= 10:
                existing["ok"].add(ticker)
            else:
                existing["incomplete"].add(ticker)

    return existing


# ── Main pipeline ──────────────────────────────────────────────────────────

def fetch_all(
    tickers: List[str],
    period: str = "10y",
    resume: bool = False,
) -> Dict:
    """Main bulk fetch pipeline.
    
    Args:
        tickers: List of ticker symbols to download.
        period: yfinance period string ("5y", "10y", "20y").
        resume: If True, skip tickers already in checkpoint.
    
    Returns: summary dict with counts.
    """
    logger.info(f"Starting bulk OHLCV fetch: {len(tickers)} tickers, period={period}")

    # Load checkpoint
    state = load_checkpoint() if resume else {"completed": {"tickers": set(), "failed": set()}, "failed": {}, "started_at": None, "last_updated": None}

    if state["started_at"] is None:
        state["started_at"] = datetime.now(timezone.utc).isoformat()

    # Identify what to fetch
    completed = set(state.get("completed", {}).get("tickers", []))
    failed = set(state.get("completed", {}).get("failed", []))

    if resume:
        remaining = [t for t in tickers if t not in completed and t not in failed]
    else:
        remaining = list(set(tickers))  # deduplicate

    logger.info(f"Remaining to fetch: {len(remaining)} (completed: {len(completed)}, failed: {len(failed)})")

    if not remaining:
        logger.info("Nothing to fetch — all done!")
        return {"remaining": 0, "completed": len(completed), "failed": len(failed)}

    # Fetch in batches
    total_fetched = 0
    batch_start = time.time()

    for i in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        # Send batch directly — _fetch_batch handles failures internally
        results = _fetch_batch(batch, period)

        for ticker, df in results.items():
            save_parquet(ticker, df)
            completed.add(ticker)
            total_fetched += 1

        # Track failures (tickers in batch but not in results)
        for ticker in batch:
            if ticker not in completed and ticker not in failed:
                failed.add(ticker)
                state["failed"][ticker] = {"reason": "fetch_failed", "time": datetime.now(timezone.utc).isoformat()}  # type: ignore

        # Save checkpoint (store sets as lists for JSON)
        state["completed"] = {"tickers": list(completed), "failed": list(failed)}
        save_checkpoint(state)

        # Progress reporting
        elapsed = time.time() - batch_start
        progress = (i + len(batch)) / len(remaining) * 100
        eta = elapsed / progress * (100 - progress) if progress > 0 else 0
        logger.info(
            f"  Batch {batch_num}/{(len(remaining)+BATCH_SIZE-1)//BATCH_SIZE}: "
            f"fetched {len(results)}/{len(batch)} | total: {total_fetched} | "
            f"progress: {progress:.0f}% | eta: {eta:.0f}s"
        )

        # Rate limiting between batches
        time.sleep(BATCH_DELAY)

    return {
        "remaining": 0,
        "completed": len(completed),
        "failed": len(failed),
        "newly_fetched": total_fetched,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bulk OHLCV fetcher for Rocket Stock Scanner")
    parser.add_argument("--period", default="10y", help="yfinance period (5y, 10y, 20y)")
    parser.add_argument("--regions", nargs="+", help="Regions to fetch (default: all)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Max parallel workers (default: {MAX_WORKERS})")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help=f"Batch size (default: {BATCH_SIZE})")
    parser.add_argument("--dry-run", action="store_true", help="Just show what would be fetched")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Load tickers
    tickers = load_universe(args.regions)
    logger.info(f"Loaded {len(tickers)} tickers from universe cache")

    if args.dry_run:
        # Show region breakdown
        with open(UNIVERSE_CACHE) as f:
            data = json.load(f)
        print(f"\n{'Region':<20} {'Tickers':>10}")
        print("-" * 32)
        for region, t_list in sorted(data["tickers"].items()):
            print(f"{region:<20} {len(t_list):>10}")
        print("-" * 32)
        print(f"{'TOTAL':<20} {len(tickers):>10}")
        return

    # Fetch
    summary = fetch_all(tickers, period=args.period, resume=args.resume)

    print(f"\n{'='*50}")
    print("  FETCH COMPLETE")
    print(f"{'='*50}")
    print(f"  Completed: {summary['completed']:,} tickers")
    print(f"  Failed:    {summary['failed']:,} tickers")
    print(f"  Newly:     {summary['newly_fetched']:,} tickers")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
