"""Daily push: scan top N stocks and send top 10 to Telegram.

Fast version — uses the full rocket scoring engine but only on the
most important tickers (~100). Runs in ~2–3 minutes vs ~8h for full scan.

Usage:
    python scripts/daily_push.py            # run scan + send
    python scripts/daily_push.py --dry-run  # print message without sending
    python scripts/daily_push.py --top 20   # show top 20 instead of 10
    python scripts/daily_push.py --options  # enable options factor (US only)
"""
import argparse
import asyncio
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/srv/svarkor/builds/rocket-stock-scanner")
os.chdir("/srv/svarkor/builds/rocket-stock-scanner")

import yfinance as yf
import pandas as pd
from rocket.data.class_share import request_symbol
from rocket.data.class_share import request_batch, land_results

from telegram import Bot
from dotenv import load_dotenv

# Load bot token
load_dotenv("config/scan_pro.env")
BOT_TOKEN = os.environ.get("SCAN_PRO_TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    load_dotenv(os.path.expanduser("~/.hermes/.env"))
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# SECURITY (MC#939): no hardcoded fallback; 0 = unset, checked at startup below.
ADMIN_CHAT_ID = int(os.environ.get("SCAN_PRO_ADMIN_CHAT_ID", "0"))

# ---------------------------------------------------------------------------
# Top ~100 tickers: mega + large cap US + major international
# ---------------------------------------------------------------------------
MAJOR_TICKERS = [
    # US mega-cap
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA",
    # US large-cap
    "BRK.B", "JPM", "JNJ", "V", "PG", "UNH", "HD", "CVX", "MA", "DIS",
    "PFE", "BAC", "XOM", "ABBV", "COST", "MRK", "PEP", "KO", "AVGO",
    "TMO", "CSCO", "MCD", "ACN", "ABT", "CRM", "DHR", "NFLX", "AMD",
    "NKE", "WMT", "LLY", "ORCL", "INTC", "VZ", "ADBE", "TXN", "QCOM",
    "PM", "RTX", "HON", "UNP", "BMY", "SBUX", "ISRG", "GS", "BLK",
    "SPGI", "C", "AXP", "TJX", "BKNG", "MDLZ", "GILD", "CVS", "LOW",
    "MO", "SCHW", "SYK", "ADP", "MMM", "CB", "CI", "USB", "ZTS",
    "REGN", "PNC", "TGT", "CL", "EQIX", "SNPS", "CDNS", "MAR",
    "F", "GM", "AMAT", "LRCX", "MRVL", "ADI", "PYPL", "SHOP",
    "UBER", "ABNB", "COIN", "PLTR",
    # Sweden
    "VOLVO-B.ST", "ERIC-B.ST", "HEXA-B.ST", "SEC-A.ST",
    "SAND-B.ST", "ITU-A.ST", "AGN-A.ST",
    # International
    "ASML.AS", "NESN.ZU", "ROG-DE", "7203.T", "9988.HK",
    "RELIANCE.NS", "TCS.NS", "INFY.NS",
]

REGION_MAP = {
    "usa": "usa",
    "sweden": "sweden",
    "denmark": "denmark",
    "finland": "finland",
    "norway": "norway",
    "germany": "germany",
    "france": "france",
    "uk": "uk",
    "japan": "japan",
    "hongkong": "hongkong",
    "china": "china",
    "india": "india",
    "korea": "korea",
    "switzerland": "switzerland",
    "international": "usa",
}

# Auto-detect region from ticker suffix
TICKER_SUFFIXES = {
    ".ST": "sweden", ".SX": "sweden",
    ".AS": "international", ".AMS": "international",
    ".DE": "germany",
    ".F": "germany",
    ".PA": "france", ".PAR": "france",
    ".MX": "international", ".MC": "france",
    ".SW": "switzerland", ".ZU": "switzerland",
    ".T": "japan", ".TK": "japan",
    ".HK": "hongkong",
    ".NS": "india", ".BO": "india",
}

def _detect_region(ticker: str) -> str:
    """Detect region from ticker suffix. Falls back to 'usa'."""
    for suffix, region in TICKER_SUFFIXES.items():
        if ticker.endswith(suffix):
            return region
    return "usa"

# ---------------------------------------------------------------------------
# Batch scanner — uses yf.download() for speed (~7s for 100+ tickers)
# ---------------------------------------------------------------------------
def batch_fetch_history(tickers: list, region_map: dict) -> dict:
    """Batch download 1y history for all tickers. Returns {ticker: DataFrame}."""
    # Split into chunks of 60 (Yahoo limit for batch requests)
    results = {}
    chunk_size = 60
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        requests, owner = request_batch(chunk)   # MC 10761 form, REUSED (T38)
        try:
            df = yf.download(requests, period="1y", interval="1d",
                             progress=False, timeout=60, group_by="ticker",
                             threads=True)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    frames = {t: df[t] for t in requests
                              if t in df.columns.get_level_values(0)}
                    results.update(land_results(frames, owner))
        except Exception:
            pass
    return results

def scan_single_ticker(ticker: str, df: pd.DataFrame, region: str,
                       enable_options: bool = False):
    """Scan one ticker using the pre-fetched history DataFrame.

    Returns (ticker, signal, score, regime, buy_count, sell_count, reason, opts_data) or None.
    """
    try:
        from rocket.data.models import TickerInfo, Region

        if df is None or df.empty or len(df) < 60:
            return None
        if "Close" not in df.columns:
            return None

        close_series = df["Close"].astype(float)
        close_series = close_series[close_series > 0]
        if len(close_series) < 50:
            return None

        price = float(close_series.iloc[-1])
        vol = df.get("Volume", pd.Series([0] * len(df)))
        avg_vol = float(vol.mean())

        # Fetch basic info (parallel via download metadata)
        name = ticker
        sector = "Unknown"
        market_cap = 0
        try:
            stock = yf.Ticker(request_symbol(ticker))
            info = stock.info or {}
            name = info.get("shortName", "") or info.get("longName", ticker)
            sector = info.get("sector", "Unknown")
            market_cap = info.get("marketCap", 0) or 0
        except Exception:
            pass

        # Map region string to enum
        region_enum = Region.US  # default
        if "sweden" in region or region.endswith(".ST"):
            region_enum = Region.EU
        elif "europe" in region or any(region.endswith(s) for s in [".DE", ".F", ".PA", ".AS", ".MX", ".SW", ".ZU"]):
            region_enum = Region.EU
        elif "asia" in region or region.endswith(".T"):
            region_enum = Region.ASIA

        ti = TickerInfo(
            ticker=ticker, name=name if name else ticker,
            sector=sector, region=region_enum,
            market_cap=market_cap, avg_volume=avg_vol,
        )

        # Use full rocket scoring
        from rocket.scoring.rocket_score import compute_rocket_score
        result = compute_rocket_score(
            df, ti, current_price=price,
            social_sentiment=False, options_factor=enable_options
        )
        if result is None:
            return None

        rocket_signal = result.get("rocket_signal")
        if rocket_signal:
            signal = rocket_signal.direction
            final_score = float(rocket_signal.final_score)
            regime = rocket_signal.regime
            reason = rocket_signal.reason
            family_votes = rocket_signal.family_votes or []
        else:
            dr = result.get("direction_result")
            final_score = float(getattr(dr, "score", 0)) if dr else 0
            signal = "HOLD"
            regime = "UNKNOWN"
            reason = ""
            family_votes = []

        # Normalize signal names
        if signal == "BULLISH":
            signal = "BUY"
        elif signal == "BEARISH":
            signal = "SELL"

        buy_count = sum(1 for fv in family_votes if fv.get("vote") == "BUY")
        sell_count = sum(1 for fv in family_votes if fv.get("vote") == "SELL")

        opts_data = result.get("options_data")

        return (ticker, signal, final_score, regime, buy_count, sell_count, reason, opts_data)

    except Exception as e:
        print(f"  ✗ {ticker}: {str(e)[:60]}", flush=True)
        return None

# ---------------------------------------------------------------------------
# Scan loop — batch fetch + per-ticker scoring
# ---------------------------------------------------------------------------
def scan_major_tickers(enable_options=False):
    """Scan major tickers using batch download. Returns sorted results."""
    print(f"Scanning {len(MAJOR_TICKERS)} tickers...", flush=True)
    start = time.time()

    # Step 1: Batch download all history (~7s for 100 tickers)
    print("  Downloading 1y history (batch)...", flush=True)
    history = batch_fetch_history(MAJOR_TICKERS, REGION_MAP)
    print(f"  History fetched: {len(history)}/{len(MAJOR_TICKERS)} tickers in {time.time()-start:.1f}s", flush=True)

    # Step 2: Score each ticker using pre-fetched data
    results = []
    scored = 0
    for i, ticker in enumerate(MAJOR_TICKERS):
        region = _detect_region(ticker)
        df = history.get(ticker)  # type: ignore
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            continue
        result = scan_single_ticker(ticker, df, region, enable_options=enable_options)
        if result:
            results.append(result)
            scored += 1

        if len(results) % 20 == 0:
            print(f"  {len(results)} tickers scored ({time.time() - start:.0f}s)", flush=True)

    elapsed = time.time() - start
    print(f"{scored} tickers scored in {elapsed:.0f}s", flush=True)

    # Sort by score descending (absolute value, strongest signals first)
    results.sort(key=lambda x: abs(x[2]), reverse=True)
    return results

# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------
def format_message(results, top_n=10):
    """Format top N results into a Telegram message."""
    top = results[:top_n]
    total = len(results)
    
    buy_count = sum(1 for _, s, _, _, _, _, _, _ in top if s == "BUY")
    sell_count = sum(1 for _, s, _, _, _, _, _, _ in top if s == "SELL")
    hold_count = top_n - buy_count - sell_count
    
    # Clean ticker display names
    def clean_ticker(ticker):
        display = ticker
        if ticker.endswith(".ST"):
            display = ticker[:-3] + "(SE)"
        elif ticker.endswith(".NS"):
            display = ticker[:-3] + "(IND)"
        elif ticker.endswith(".HK"):
            display = ticker[:-3] + "(HK)"
        elif ticker.endswith(".T"):
            display = ticker[:-2] + "(JP)"
        elif ticker.endswith(".AS"):
            display = ticker[:-3] + "(NL)"
        elif ticker.endswith(".DE"):
            display = ticker[:-3] + "(DE)"
        elif ticker.endswith(".ZU"):
            display = ticker[:-3] + "(CH)"
        return display
    
    lines = [
        "Rocket Scanner — Dagliga Top 10",
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Scannade {total} tickers (Rocket Scoring Engine v2)",
        "",
        "Top signals:",
        f"BUY: {buy_count} | SELL: {sell_count} | HOLD: {top_n - buy_count - sell_count}",
        "",
    ]
    
    for rank, (ticker, signal, score, regime, bc, sc, reason, opts) in enumerate(top, 1):
        # Color indicator
        if score >= 0.3:
            emoji = "🟢"
        elif score <= -0.3:
            emoji = "🔴"
        else:
            emoji = "⚪"
        
        clean = clean_ticker(ticker)
        
        lines.append(f"{rank}. {emoji} *{clean}* — {signal}")
        lines.append(f"   ⭐ Score: {score:+.3f} | Regime: {regime}")
        
        if reason:
            # Truncate long reasons
            reason_short = reason if len(reason) < 80 else reason[:77] + "..."
            lines.append(f"   ↳ {reason_short}")
        
        # Options data for US tickers
        if opts and "max_pain_strike" in opts:
            mp = opts.get("max_pain_strike", "?")
            cr = opts.get("put_call_ratio", "?")
            dte = opts.get("dte", "?")
            bias = opts.get("bias", 0)
            lines.append(f"   📊 MP={mp} PCR={cr:.3f} DTE={dte} bias={bias:+.3f}")
        
        lines.append("")
    
    lines.append("⚠️ Resultaten är tekniska observationer och ska inte")
    lines.append("tolkas som finansiella råd eller prognoser.")
    lines.append(f"🕒 Uppdaterad: {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Database storage
# ---------------------------------------------------------------------------
def store_results(results: list, batch_id: int = None):
    """Store scan results in SQLite."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "signals.db"
    if not db_path.exists():
        print(f"  Database not found at {db_path}, skipping storage", flush=True)
        return
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Get next batch_id
    cur.execute("SELECT COALESCE(MAX(batch_id), 0) + 1 FROM batch_results")
    if batch_id is None:
        batch_id = cur.fetchone()[0]
    
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for ticker, signal, score, regime, buy_count, sell_count, reason, opts in results:
        try:
            cur.execute("""
                INSERT OR REPLACE INTO batch_results
                (batch_id, ticker, score, status, scanned_at)
                VALUES (?, ?, ?, ?, ?)
            """, (batch_id, ticker, score, signal.lower(), now))
            inserted += 1
        except Exception as e:
            print(f"  DB insert failed for {ticker}: {str(e)[:60]}", flush=True)
    
    conn.commit()
    conn.close()
    print(f"  Stored {inserted} results to batch_id={batch_id}", flush=True)

# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------
def send_telegram_message(bot_token, chat_id, text):
    """Send message via Telegram Bot API."""
    try:
        bot = Bot(token=bot_token)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            )
        finally:
            loop.close()
        return True
    except Exception as e:
        print(f"Telegram send failed: {str(e)[:100]}", flush=True)
        return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Rocket Scanner Daily Push")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print message without sending")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of signals to show (default: 10)")
    parser.add_argument("--options", action="store_true",
                        help="Enable options factor for US tickers")
    args = parser.parse_args()
    
    if not BOT_TOKEN:
        print("❌ No Telegram bot token found!", flush=True)
        print("Set SCAN_PRO_TELEGRAM_BOT_TOKEN in config/scan_pro.env", flush=True)
        sys.exit(1)

    if not ADMIN_CHAT_ID:
        print("❌ No admin chat id configured!", flush=True)
        print("Set SCAN_PRO_ADMIN_CHAT_ID in config/scan_pro.env (untracked - NEVER commit it)", flush=True)
        sys.exit(1)
    
    # Scan
    print("🚀 Rocket Scanner — Daily Push", flush=True)
    results = scan_major_tickers(enable_options=args.options)
    
    # Store in database
    store_results(results)
    
    if not results:
        print("❌ No tickers scored!", flush=True)
        sys.exit(1)
    
    # Format
    message = format_message(results, args.top)
    
    if args.dry_run:
        print("\n" + "=" * 60)
        print("📝 DRY RUN — Telegram message:")
        print("=" * 60)
        print(message)
        print("\n[Dry run complete — no message sent]", flush=True)
        return
    
    # Send
    print("\n📤 Sending to Telegram...", flush=True)
    ok = send_telegram_message(BOT_TOKEN, ADMIN_CHAT_ID, message)
    if ok:
        print(f"✅ Top 10 sent to Telegram!", flush=True)
        print(f"📊 {len(results)} tickers scanned!", flush=True)
    else:
        print("❌ Telegram send failed!", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
