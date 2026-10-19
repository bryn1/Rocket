#!/usr/bin/env python3
"""Fetch 10+ years of daily OHLCV data for all 25k+ tickers and store as Parquet.

Usage:
    python scripts/fetch_history.py              # Fetch all regions
    python scripts/fetch_history.py --region usa --region sweden  # Only specific regions
    python scripts/fetch_history.py --resume     # Resume from checkpoint
    python scripts/fetch_history.py --verify     # Verify data integrity
"""
import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')
logging.getLogger('yahooquery').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

DATA_DIR = Path('data/ohlcv')
DATA_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_FILE = DATA_DIR / 'ohlcv_10y.parquet'
CHECKPOINT_FILE = DATA_DIR / 'fetch_checkpoint.json'
BATCH_SIZE = 100
DELAY_BETWEEN_CALLS = 0.3  # seconds between requests
REQUEST_TIMEOUT = 10  # seconds per request
MAX_WORKERS = 8  # parallel workers for fetching


def load_universe(regions=None):
    """Load ticker universe from rocket."""
    sys.path.insert(0, '.')
    from rocket.data.universe_builder import get_universe
    
    u = get_universe()
    if regions:
        tickers = []
        for r in regions:
            if r in u:
                tickers.extend(u[r])
    else:
        tickers = []
        for r, t_list in u.items():
            tickers.extend(t_list)
    
    # Build region lookup map
    region_map = {}
    for r, t_list in u.items():
        for t in t_list:
            region_map[t] = r
    
    return u, sorted(set(t for t in tickers if t.strip())), region_map


def _fetch_single_ticker(ticker: str, region: str, timeout: int = 10) -> tuple:
    """Fetch 10y history for a single ticker.
    
    Returns: (ticker, region, df_or_None, error_or_None)
    """
    try:
        from rocket.data.class_share import request_symbol
        stock = yf.Ticker(request_symbol(ticker))
        df = stock.history(period='10y', interval='1d', timeout=timeout)
        
        if df is not None and not df.empty:
            # Normalize columns
            df = df.copy()
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']
            
            # Handle Date - could be index or column
            if 'Date' in df.columns:
                df = df.set_index('Date')
            
            # Ensure index is datetime
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            
            df.index.name = 'Date'
            df = df.reset_index()  # Now Date is a column
            df['Ticker'] = ticker
            df['Region'] = region
            
            # Select only essential columns
            df = df[['Date', 'Ticker', 'Region', 'Open', 'High', 'Low', 'Close', 'Volume']]
            
            # Remove duplicates
            df = df.drop_duplicates(subset=['Date', 'Ticker'])
            df = df.sort_values('Date')
            
            return (ticker, region, df, None)
        else:
            return (ticker, region, None, "No data")
    except Exception as e:
        return (ticker, region, None, str(e)[:100])


def load_checkpoint() -> dict:
    """Load checkpoint."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {'processed': [], 'total': 0}


def save_checkpoint(tickers_processed: list, total: int):
    """Save checkpoint to resume later."""
    checkpoint = {
        'processed': tickers_processed,
        'total': total,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description='Fetch 10y OHLCV data for all tickers')
    parser.add_argument('--region', nargs='+', help='Specific regions to fetch (default: all)')
    parser.add_argument('--resume', action='store_true', help='Resume from checkpoint')
    parser.add_argument('--verify', action='store_true', help='Verify data integrity')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='Batch size')
    parser.add_argument('--workers', type=int, default=MAX_WORKERS, help='Parallel workers')
    args = parser.parse_args()
    
    print(f"🚀 OHLCV Fetcher — 10+ years daily data")
    print(f"{'='*50}")
    
    # Load universe
    u, all_tickers, region_map = load_universe(args.region)
    total = len(all_tickers)
    print(f"📊 Universe: {total} tickers")
    print(f"💾 Output: {PARQUET_FILE}")
    print()
    
    # Check for resume
    if args.resume:
        checkpoint = load_checkpoint()
        processed_set = set(checkpoint['processed'])
        remaining = [t for t in all_tickers if t not in processed_set]
        print(f"🔄 Resuming from checkpoint: {len(processed_set)} already processed, {len(remaining)} remaining")
        all_tickers = remaining
        if not all_tickers:
            print("✅ All tickers already processed!")
            return
    else:
        # Clear checkpoint if not resuming
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
    
    # Fetch data
    print(f"⏳ Fetching {len(all_tickers)} tickers ({args.workers} parallel workers, {args.batch_size} per batch report)...")
    start_time = time.time()
    
    all_dfs = []
    tickers_processed = []
    success_count = 0
    fail_count = 0
    
    for batch_start in range(0, len(all_tickers), args.batch_size):
        batch = all_tickers[batch_start:batch_start + args.batch_size]
        batch_num = batch_start // args.batch_size + 1
        
        # Fetch this batch using ThreadPoolExecutor
        batch_results = []
        with ThreadPoolExecutor(max_workers=min(args.workers, len(batch))) as executor:
            futures = {
                executor.submit(_fetch_single_ticker, ticker, region_map.get(ticker, 'usa'), REQUEST_TIMEOUT): ticker
                for ticker in batch
            }
            for future in as_completed(futures):
                result = future.result()
                ticker = result[0]
                if result[2] is not None and not result[2].empty:
                    all_dfs.append(result[2])
                    tickers_processed.append(ticker)
                    success_count += 1
                else:
                    fail_count += 1
                    if success_count % 500 == 0:
                        print(f"  ⚠️  {ticker}: {result[3][:60]}")
                
                # Rate limiting between submissions
                time.sleep(DELAY_BETWEEN_CALLS)
        
        elapsed = time.time() - start_time
        processed_so_far = min(batch_start + args.batch_size, len(all_tickers))
        total_batches = (len(all_tickers) + args.batch_size - 1) // args.batch_size
        rate = processed_so_far / elapsed if elapsed > 0 else 0
        eta = (len(all_tickers) - processed_so_far) / rate if rate > 0 else 0
        
        print(f"  ✅ Batch {batch_num}/{total_batches}: {processed_so_far}/{len(all_tickers)} tickers, "
              f"{len(all_dfs)} dataframes ({success_count} success, {fail_count} failed), "
              f"{elapsed:.0f}s, {rate:.1f} tickers/s, ETA: {eta:.0f}s")
        
        # Save checkpoint
        save_checkpoint(tickers_processed, total)
    
    # Concatenate and save
    if all_dfs:
        print(f"\n📊 Concatenating {len(all_dfs)} dataframes...")
        df_all = pd.concat(all_dfs, ignore_index=True)
        
        # Sort and dedupe
        df_all = df_all.sort_values(['Ticker', 'Date'])
        df_all = df_all.drop_duplicates(subset=['Ticker', 'Date'])
        
        print(f"💾 Saving to Parquet: {df_all.shape[0]:,} rows x {df_all.shape[1]} columns")
        df_all.to_parquet(PARQUET_FILE, engine='pyarrow', index=False)
        
        file_size_mb = PARQUET_FILE.stat().st_size / (1024 * 1024)
        print(f"💾 File size: {file_size_mb:.1f} MB")
        
        # Print summary
        print(f"\n{'='*50}")
        print(f"✅ FETCH COMPLETE!")
        print(f"   Tickers: {df_all['Ticker'].nunique():,}")
        print(f"   Rows: {df_all.shape[0]:,}")
        print(f"   Date range: {df_all['Date'].min()} to {df_all['Date'].max()}")
        print(f"   Success: {success_count}, Failed: {fail_count}")
        print(f"   File: {PARQUET_FILE} ({file_size_mb:.1f} MB)")
    else:
        print(f"❌ No data fetched!")
        return
    
    elapsed = time.time() - start_time
    print(f"\n⏱️  Total time: {elapsed:.0f}s ({len(all_tickers)/elapsed:.1f} tickers/s)")
    
    # Clean up checkpoint
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


if __name__ == '__main__':
    main()
