"""Scan a single batch of tickers — multi-threaded, no rate limiting.

Uses yahooquery.Ticker (with yfinance fallback).

Usage:
    python scripts/scan_batch.py --batch data/batches/batch_0000.json --batch-id 0

Outputs:
    data/batches/batch_0000_results.json — scored results for this batch
    appends to /tmp/scanall.log for real-time monitoring
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

warnings.filterwarnings('ignore')
logging.getLogger('yahooquery').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

LOG_FILE = '/tmp/scanall.log'
_log_fh = open(LOG_FILE, 'a')


def _log(msg, flush=True):
    line = str(msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        _log_fh.write(line + '\n')
        if flush:
            _log_fh.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# YahooQuery helpers
# ---------------------------------------------------------------------------

def _normalize_yq_history(df):
    """Normalize YahooQuery history DataFrame to yfinance-compatible format.

    YahooQuery returns a DataFrame with a MultiIndex (symbol, date) and
    lowercase column names (open, high, low, close, volume, ...).
    This function flattens the index and capitalizes columns so the
    resulting DataFrame is compatible with compute_score().
    """
    if df is None or df.empty:
        return df
    result = df.copy()
    result.columns = [c.capitalize() for c in result.columns]
    result.index = pd.to_datetime(result.index.get_level_values('date'))
    return result


def _fetch_history(ticker, timeout=15):
    """Fetch 10-year history.

    Primary: yahooquery.Ticker.history (10y period, then 2016-2026 fallback).
    Fallback: yfinance if YahooQuery fails or returns too little data.

    Returns a pandas DataFrame with columns [Open, High, Low, Close, Volume, ...]
    and a DatetimeIndex, or None on failure.
    """
    try:
        import yahooquery as yq
        tq = yq.Ticker(ticker, progress=False)

        # Primary: 10y period
        df = tq.history(period='10y', interval='1d')
        df = _normalize_yq_history(df)
        if df is not None and len(df) >= 100:
            return df

        # Fallback: explicit date range
        df = tq.history(start='2016-01-01', end='2026-07-24', interval='1d')
        df = _normalize_yq_history(df)
        if df is not None and len(df) > 0:
            return df

    except Exception:
        pass

    # Fallback to yfinance
    try:
        import yfinance as yf
        from rocket.data.class_share import request_symbol
        return yf.Ticker(request_symbol(ticker)).history(period='10y', timeout=timeout)
    except Exception:
        return None


def _normalize_info(raw):
    """Normalize a YahooQuery info dict to be compatible with the existing code.

    YahooQuery uses different key names than yfinance:
        - regularMarketPrice  → currentPrice
        - regularMarketVolume → volume
        - regularMarketOpen   → open
        - regularMarketDayHigh → dayHigh
        - regularMarketDayLow  → dayLow
    This function adds yfinance-compatible keys as aliases so existing
    scan_ticker code (which uses .get('currentPrice'), .get('volume'), etc.)
    works without changes.
    """
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    # Price aliases
    out.setdefault('currentPrice', out.get('regularMarketPrice', 0))
    out.setdefault('open', out.get('regularMarketOpen', 0))
    out.setdefault('dayHigh', out.get('regularMarketDayHigh', 0))
    out.setdefault('dayLow', out.get('regularMarketDayLow', 0))
    # Volume aliases
    out.setdefault('volume', out.get('regularMarketVolume', 0))
    out.setdefault('averageVolume', out.get('averageDailyVolume3Month', 0))
    out.setdefault('averageVolume10days', out.get('averageDailyVolume10Day', 0))
    return out


def _fetch_info(ticker, timeout=5):
    """Fetch company info.

    Primary: yahooquery.Ticker.summary_profile (sector) + quotes (price/volume).
    Fallback: yfinance.Ticker.info.

    Returns a dict with keys like 'shortName', 'sector', 'currentPrice', 'volume'.
    """
    try:
        import yahooquery as yq
        tq = yq.Ticker(ticker, progress=False)

        # Get sector from summary_profile
        profile = tq.summary_profile
        info = {}
        if isinstance(profile, dict) and ticker in profile:
            info = dict(profile[ticker])
            info['sector'] = info.get('sector', 'Unknown')
            info['industry'] = info.get('industry', '')

        # Get price/volume from quotes
        quotes = tq.quotes
        if isinstance(quotes, dict) and ticker in quotes:
            info['shortName'] = quotes[ticker].get('shortName', '')
            info['longName'] = quotes[ticker].get('longName', '')
            info['currentPrice'] = quotes[ticker].get('regularMarketPrice', 0)
            info['volume'] = quotes[ticker].get('regularMarketVolume', 0)
            info['open'] = quotes[ticker].get('regularMarketOpen', 0)
            info['dayHigh'] = quotes[ticker].get('regularMarketDayHigh', 0)
            info['dayLow'] = quotes[ticker].get('regularMarketDayLow', 0)
            info['averageVolume'] = quotes[ticker].get('averageDailyVolume3Month', 0)
            info['averageVolume10days'] = quotes[ticker].get('averageDailyVolume10Day', 0)

        if info:
            return info
    except Exception:
        pass

    # Fallback to yfinance
    try:
        import yfinance as yf
        from rocket.data.class_share import request_symbol
        info = yf.Ticker(request_symbol(ticker)).info
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_score(ticker_symbol, hist):
    """Compute a composite score from technical indicators."""
    if hist is None or len(hist) < 20:
        return 0.0, {'status': 'insufficient_data'}

    prices = hist['Close'].dropna()
    if len(prices) < 20:
        return 0.0, {'status': 'insufficient_data'}

    score = 0.0
    factors = 0

    # 1. Momentum (40% weight) — 20-day return
    if prices.iloc[-1] > 0 and prices.iloc[-20] > 0:
        mom = (prices.iloc[-1] - prices.iloc[-20]) / prices.iloc[-20]
        score += max(-0.5, min(0.5, mom * 5))
        factors += 1

    # 2. Trend (30% weight) — EMA alignment
    if len(prices) >= 20:
        ema20 = prices.rolling(20).mean().iloc[-1]
        ema50 = prices.rolling(50).mean().iloc[-1] if len(prices) >= 50 else ema20
        current = prices.iloc[-1]
        if ema20 > 0:
            if current > ema20:
                score += 0.15
            else:
                score -= 0.15
            if len(prices) >= 50 and ema20 > ema50:
                score += 0.15
            elif len(prices) >= 50:
                score -= 0.15
        factors += 1

    # 3. Volatility (20% weight) — low vol = more stable
    if len(prices) >= 20:
        vol = prices.pct_change().std()
        if vol > 0:
            score += 0.3 - min(vol * 3, 0.3)
        factors += 1

    # 4. Volume trend (10% weight)
    if 'Volume' in hist.columns and len(hist) >= 20:
        vol_series = hist['Volume'].dropna()
        if len(vol_series) >= 10:
            recent_vol = vol_series.iloc[-5:].mean()
            older_vol = vol_series.iloc[-20:-10].mean()
            if older_vol > 0:
                if recent_vol / older_vol > 1.2:
                    score += 0.05
                elif recent_vol / older_vol < 0.8:
                    score -= 0.05
        factors += 1

    if factors > 0:
        score = score / factors
    return max(-1.0, min(1.0, score)), {'factors_used': factors}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_ticker(region, ticker):
    """Scan one ticker."""
    full = ticker
    if region != 'usa':
        full = f"{ticker}.{region.upper()}"

    result = {
        'region': region, 'ticker': ticker, 'full_ticker': full,
        'score': 0.0, 'score_details': {}, 'name': '',
        'sector': 'Unknown', 'price': 0.0, 'volume': 0,
        'status': 'failed', 'error': None,
    }

    try:
        # Fetch history
        hist = _fetch_history(full, timeout=15)
        if hist is None or len(hist) < 10:
            result['status'] = 'insufficient_data'
            return result

        # Mark tickers with limited history (< 500 trading days ≈ ~2 years)
        if len(hist) < 500:
            result['status'] = 'limited_history'

        # Fetch info
        info = _fetch_info(full, timeout=5)

        result['name'] = info.get('shortName', '') or info.get('longName', ticker)
        result['sector'] = info.get('sector', 'Unknown')
        result['price'] = float(info.get('currentPrice', 0) or 0)
        result['volume'] = int(info.get('volume', 0) or 0)

        score, details = compute_score(full, hist)
        result['score'] = score
        result['score_details'] = details
        result['status'] = 'scored'

    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)[:200]

    return result


def main():
    parser = argparse.ArgumentParser(description="Scan a single batch of tickers")
    parser.add_argument("--batch", required=True, help="Path to batch JSON file")
    parser.add_argument("--batch-id", type=int, required=True, help="Batch ID number")
    parser.add_argument("--workers", type=int, default=10, help="Max concurrent workers")
    args = parser.parse_args()

    with open(args.batch) as f:
        tickers = json.load(f)

    _log(f"Scan batch {args.batch_id}: {len(tickers)} tickers", flush=True)

    results = []
    scored = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for t in tickers:
            fut = pool.submit(scan_ticker, t['region'], t['ticker'])
            futures[fut] = t

        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r['status'] == 'scored':
                scored += 1

            count = len(results)
            if count % 250 == 0:
                elapsed = time.time() - t0
                rate = count / elapsed if elapsed > 0 else 0
                _log(f"  Batch {args.batch_id}: {count}/{len(tickers)}, "
                     f"{scored} scored, {elapsed:.0f}s ({rate:.1f}/s)", flush=True)

    elapsed = time.time() - t0
    rate = len(tickers) / elapsed if elapsed > 0 else 0

    # Save results
    out = Path(args.batch).parent / f"batch_{args.batch_id}_results.json"
    with open(out, 'w') as f:
        json.dump(results, f, indent=2)

    _log(f"  Batch {args.batch_id} DONE: {scored}/{len(tickers)} scored, "
         f"{elapsed:.0f}s ({rate:.1f}/s) -> {out}", flush=True)

    # Save to SQLite
    db_path = Path('data/signals.db')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS batch_results (
        batch_id INTEGER, ticker TEXT, region TEXT, full_ticker TEXT,
        score REAL, status TEXT, name TEXT, sector TEXT,
        price REAL, volume INTEGER, score_details TEXT, error TEXT,
        scanned_at TEXT
    )''')
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        c.execute(
            'INSERT OR REPLACE INTO batch_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (args.batch_id, r['ticker'], r.get('region',''), r.get('full_ticker',''),
             r['score'], r['status'], r.get('name',''), r.get('sector',''),
             r.get('price',0), r.get('volume',0),
             json.dumps(r.get('score_details',{})), r.get('error',''), now)
        )
    conn.commit()
    conn.close()

    _log(f"  Batch {args.batch_id} saved to SQLite", flush=True)
    print(f"Batch {args.batch_id}: {scored}/{len(tickers)} scored in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
