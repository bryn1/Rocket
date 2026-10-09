# reason: shared legacy universe builder (>600 lines, grandfathered) — MC 10264
# F2 touches it with ONLY the additive write_cache param; a split-by-concern
# pass is scheduled hygiene, not this card.
"""
Rocket Stock Scanner - Dynamic Universe Builder

Loads ticker universes from:
1. Curated US ticker CSV (7000+ verified NYSE/NASDAQ/AMEX tickers)
2. Major index constituents via Wikipedia (OMX30, FTSE, DAX, etc.)
3. Cached for performance

Total target: 15,000+ tickers across global markets.
"""

import csv
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .universe_suffix import apply_region_suffix

logger = logging.getLogger(__name__)

# Cache paths
CACHE_DIR = Path(__file__).parent
CACHE_FILE = CACHE_DIR / "universe_cache.json"
CACHE_TTL_HOURS = 24  # Refresh cache every 24h

# Known index constituents (fetched from Wikipedia and other sources)
INDEX_CONSTITUENTS_FILE = CACHE_DIR / "index_constituents.json"

# US tickers source: GitHub mirror of browser-vm/us-stock-tickers
# 7000+ verified US ticker symbols (NYSE, NASDAQ, AMEX)
US_TICKERS_URL = (
    "https://raw.githubusercontent.com/browser-vm/us-stock-tickers"
    "/main/us_stock_tickers.csv"
)
US_TICKERS_CACHE = CACHE_DIR / "us_tickers.csv"

# Local CSV/XLSX ticker sources (verified, structured data)
# Path: rocket-stock-scanner/data/tickers/
TICKER_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "tickers"
LOCAL_TICKER_SOURCES = {
    # Australia (ASX)
    "australia": {
        "file": TICKER_DATA_DIR / "asx_stocks.csv",
        "type": "csv",
        "ticker_column": 0,  # First column = "ASX code"
    },
    # UK (LSE) — All Equity (includes Shares, Depository Receipts, ETFs, etc.)
    "uk": {
        "file": TICKER_DATA_DIR / "lse_stocks.xlsx",
        "type": "xlsx",
        "sheet": "1.0 All Equity",
        "ticker_column": 0,  # TIDM column
    },
    # Germany (Frankfurt)
    "germany": {
        "file": TICKER_DATA_DIR / "frankfurt_stocks.xlsx",
        "type": "xlsx",
        "sheet": "Prime Standard",
        "ticker_column": "scan",  # Scan all cells for ticker-like patterns
    },
    # Other EU — All Equity (includes Shares, Depository Receipts, ETFs, etc.)
    "other_eu": {
        "file": TICKER_DATA_DIR / "other_eu_stocks.xlsx",
        "type": "xlsx",
        "sheet": "1.0 All Equity",
        "ticker_column": 0,
    },
}

# Wikipedia pages by region: {region_key: [(page_name, description), ...]}
# page_name is the Wikipedia article slug (with %26 for &, etc.)
WIKI_PAGES = {
    # Sweden / Scandinavia
    "sweden": [
        ("OMX_Stockholm_30", "OMX Stockholm 30"),
        ("Stockholm_Stock_Exchange", "Stockholm Stock Exchange"),
        ("Oslo_Stock_Exchange", "Oslo Stock Exchange"),
    ],
    # UK
    "uk": [
        ("FTSE_100_Index", "FTSE 100"),
        ("FTSE_250_Index", "FTSE 250"),
        ("FTSE_350_Index", "FTSE 350"),
    ],
    # Germany
    "germany": [
        ("Frankfurt_Stock_Exchange", "Frankfurt Stock Exchange"),
        ("DAX", "DAX index constituents"),
    ],
    # France
    "france": [
        ("CAC_40", "CAC 40"),
        ("Euronext", "Euronext Paris"),
    ],
    # Japan
    "japan": [
        ("Nikkei_225", "Nikkei 225"),
        ("Tokyo_Stock_Exchange", "Tokyo Stock Exchange"),
    ],
    # Hong Kong
    "hongkong": [
        ("Hang_Seng_Index", "Hang Seng Index"),
        ("Hong_Kong_Stock_Exchange", "Hong Kong Stock Exchange"),
    ],
    # China
    "china": [
        ("Shanghai_Stock_Exchange", "Shanghai Stock Exchange"),
        ("Shenzhen_Stock_Exchange", "Shenzhen Stock Exchange"),
        ("Shanghai_Composite_Index", "Shanghai Composite Index"),
    ],
    # India
    "india": [
        ("NIFTY_50", "NIFTY 50"),
        ("Bombay_Stock_Exchange", "BSE / Bombay Stock Exchange"),
        ("National_Stock_Exchange_of_India", "NSE / National Stock Exchange"),
    ],
    # Australia
    "australia": [
        ("S%26P_ASX_200", "S&P ASX 200"),
        ("ASX", "Australian Securities Exchange"),
    ],
    # Canada
    "canada": [
        ("S%26P/TSX_Composite_Index", "S&P/TSX Composite"),
        ("Toronto_Stock_Exchange", "Toronto Stock Exchange"),
    ],
    # Switzerland
    "switzerland": [
        ("SIX_Swiss_Exchange", "SIX Swiss Exchange"),
        ("SMI_(index)", "Swiss Market Index (SMI)"),
    ],
    # South Korea
    "korea": [
        ("KOSPI", "KOSPI index"),
        ("Korea_Exchange", "Korea Exchange"),
    ],
}


def _load_us_tickers_from_csv() -> set[str]:
    """Load verified US ticker symbols from a curated CSV file.

    Source: https://github.com/browser-vm/us-stock-tickers
    Contains 7000+ verified ticker symbols for NYSE, NASDAQ, and AMEX.
    This is the ground-truth source for US tickers — no regex guessing.

    Returns sorted list of unique tickers (max 5 chars, optional .A/.B suffix).
    """
    # Try cached CSV first
    if US_TICKERS_CACHE.exists():
        try:
            with open(US_TICKERS_CACHE, "r") as f:
                content = f.read()
            if content and len(content) > 1000:
                logger.info(f"Loaded {US_TICKERS_CACHE.name} ({len(content)} chars)")
                return _parse_csv_content(content)
        except IOError:
            logger.warning("Failed to write local tickers cache")

    # Download CSV
    try:
        import requests
        resp = requests.get(US_TICKERS_URL, timeout=30)
        resp.raise_for_status()
        content = resp.text
        # Save cache
        with open(US_TICKERS_CACHE, "w") as f:
            f.write(content)
        logger.info(f"Downloaded US tickers CSV ({len(content)} chars)")
        return _parse_csv_content(content)
    except Exception:
        logger.warning("Failed to load US tickers CSV")
        return set()


def _parse_csv_content(content: str) -> set[str]:
    """Parse CSV content into a set of valid US tickers."""
    tickers = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("ticker_symbol"):
            continue
        # Format: TICKER,Company Name,Exchange
        parts = line.split(",")
        if parts:
            ticker = parts[0].strip().upper()
            # Valid: 1-5 uppercase letters, optionally followed by .A/.B or /B suffix
            # e.g., BRK/B, BRK.A, AAPL, MSFT
            if ticker and re.match(r"^[A-Z0-9/]{1,5}(?:[.][A-Z]{1,2})?$", ticker):
                tickers.add(ticker)
    return tickers


def _load_tickers_from_local_source(source: dict) -> list[str]:
    """Load tickers from a local CSV or XLSX file.

    Args:
        source: Dict with keys:
            file: Path to CSV/XLSX file
            type: "csv" or "xlsx"
            ticker_column: column index (int) or "scan" for full-cell scan
            sheet: XLSX sheet name (only for xlsx type)

    Returns:
        Sorted list of unique valid tickers, or empty list on failure.
    """
    filepath = source["file"]
    source_type = source["type"]
    ticker_col = source.get("ticker_column", 0)

    if not filepath.exists():
        logger.warning(f"Local source file not found: {filepath}")
        return []

    try:
        if source_type == "csv":
            return _load_tickers_from_csv_file(filepath, ticker_col)
        elif source_type == "xlsx":
            return _load_tickers_from_xlsx_file(filepath, ticker_col, source.get("sheet"))
        else:
            logger.warning(f"Unsupported local source type: {source_type}")
            return []
    except Exception:
        logger.warning("Failed to load tickers from {filepath}")
        return []


def _load_tickers_from_csv_file(filepath: Path, ticker_col: int) -> list[str]:
    """Load tickers from a CSV file using the specified column index."""
    tickers = set()
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # Skip header row
        for row in reader:
            if ticker_col < len(row):
                cell = row[ticker_col].strip().upper()
                if _TICKER_RE.match(cell) and cell not in _FALSE_POSITIVES:
                    tickers.add(cell)
    return sorted(tickers)


def _load_tickers_from_xlsx_file(filepath: Path, ticker_col, sheet_name: Optional[str] = None) -> list[str]:
    """Load tickers from an XLSX file.

    If ticker_col is an int, reads from that column index.
    If ticker_col is "scan", scans ALL cells for ticker-like patterns.
    """
    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl required for XLSX loading")
        return []

    tickers = set()
    wb = openpyxl.load_workbook(filepath, data_only=True)

    if sheet_name and sheet_name in wb.sheetnames:
        sheets_to_scan = [sheet_name]
    elif sheet_name:
        logger.warning(f"Sheet {sheet_name} not found in {filepath}, scanning all sheets")
        sheets_to_scan = wb.sheetnames
    else:
        sheets_to_scan = wb.sheetnames

    for sheet in sheets_to_scan:
        ws = wb[sheet]
        if ticker_col != "scan":
            # Read specific column
            for row in ws.iter_rows(min_row=2, values_only=True):  # Skip header
                if ticker_col < len(row) and row[ticker_col]:
                    cell = str(row[ticker_col]).strip().upper()
                    if _TICKER_RE.match(cell) and cell not in _FALSE_POSITIVES:
                        tickers.add(cell)
        else:
            # Scan all cells for ticker-like patterns
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell and isinstance(cell, str):
                        cell = cell.strip().upper()
                        if len(cell) > 20:
                            continue
                        if _TICKER_RE.match(cell) and cell not in _FALSE_POSITIVES:
                            tickers.add(cell)

    wb.close()
    return sorted(tickers)


def _is_cache_fresh(cache: dict) -> bool:
    """Check if cache is still within TTL."""
    ts = cache.get("timestamp", "")
    try:
        cached_time = datetime.fromisoformat(ts)
        return datetime.now(timezone.utc) - cached_time < timedelta(hours=CACHE_TTL_HOURS)
    except (ValueError, TypeError):
        return False


def _read_cache() -> Optional[dict]:
    """Read universe cache from disk."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _write_cache(universe: dict) -> None:
    """Write universe cache to disk with timestamp."""
    cache = {
        "tickers": universe,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "wikipedia + known constituents",
        "version": 2,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _save_index_constituents(constituents: dict[str, list[str]]) -> None:
    """Save index constituents to separate cache file."""
    with open(INDEX_CONSTITUENTS_FILE, "w") as f:
        json.dump(constituents, f, indent=2, ensure_ascii=False)


def _load_index_constituents() -> dict[str, list[str]]:
    """Load pre-fetched index constituents from cache file."""
    if not INDEX_CONSTITUENTS_FILE.exists():
        return {}
    try:
        with open(INDEX_CONSTITUENTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# Ticker regex: two strict patterns (no decimals, no pure digit strings):
# 1. No-dot: [A-Z]{1,5} — e.g. AAPL, GOOGL, RELIANCE, BF
# 2. Dot: [A-Z0-9]{1,5}(?:-[A-Z0-9]{1,5})*\.[A-Z]{1,4} — e.g. BMW.DE, 7203.T, ERIC-B.ST
# SEC tickers are max 5 chars. International tickers with suffix can be longer (handled by dot pattern).
# This rejects company names (MICROSOFT=9, APPLE=5, CISCO=5, INTEL=5, TESLA=5).
# Note: All ticker text must be uppercased before matching.
_TICKER_RE = re.compile(r"^[A-Z0-9/]{1,5}(?:[-/][A-Z0-9]{1,5})*\.[A-Z]{1,4}$|^[A-Z0-9/]{1,5}$")

# Known false positives to filter out
_FALSE_POSITIVES = frozenset({
    "N/A", "N.A.", "—", "-", "", "NA", "N/A.", "N/A ",
    "USD", "EUR", "GBP", "JPY", "CNY", "AUD", "CAD", "CHF",
    "KRW", "HKD", "SEK", "NOK", "DKK", "INR",
    "ETF", "REIT", "FUND", "INDEX", "SHARES", "UNITS",
    "CLASS",
})


def _extract_tickers_from_wikipedia(page_name: str) -> list[str]:
    """Extract ticker symbols from a Wikipedia table using broad cell scanning.

    Strategy:
    1. Fetch the Wikipedia page
    2. Find ALL wikitable tables on the page
    3. For each table, scan ALL cells (not just a specific column)
    4. Match cells against ticker regex patterns
    5. Filter out false positives
    6. Return sorted unique list of tickers

    This works for any Wikipedia table regardless of column naming.

    Returns sorted unique list of tickers.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("requests and bs4 required for Wikipedia scraping")
        return []

    url = f"https://en.wikipedia.org/wiki/{page_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Rocket Stock Scanner bot; +https://github.com/bryn1/rocket)"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.warning(f"Wikipedia page not found: {page_name}")
        else:
            logger.warning(f"Wikipedia fetch failed for {page_name}")
        return []
    except Exception:
        logger.warning(f"Wikipedia fetch failed for {page_name}")
        return []

    if resp.status_code != 200:
        logger.warning(f"Wikipedia returned status {resp.status_code} for {page_name}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all wikitable tables
    tables = soup.find_all("table", {"class": re.compile(r"wikitable")})
    if not tables:
        logger.debug(f"No wikitable found for {page_name}")
        return []

    all_tickers = set()

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        # Scan ALL cells in ALL rows (skip header row for data)
        for row in rows:
            cells = row.find_all(["td", "th"])
            for cell in cells:
                # Strip inner HTML (links, references, superscripts)
                cell_text = cell.get_text(" ", strip=True)
                # Remove reference markers like [1], [a], [nb 1]
                cell_text = re.sub(r"\[[\w\s]+\]", "", cell_text).strip()
                # Remove parenthetical notes like "(United States)"
                cell_text = re.sub(r"\(.*?\)", "", cell_text).strip()
                # Collapse whitespace
                cell_text = re.sub(r"\s+", "", cell_text)

                # Uppercase before regex match (e.g., "Telia" → "TELIA")
                cell_text_upper = cell_text.upper()

                if _TICKER_RE.match(cell_text_upper) and cell_text_upper not in _FALSE_POSITIVES:
                    all_tickers.add(cell_text_upper)

    return sorted(all_tickers)


def _build_universe(force_refresh: bool = False,
                    write_cache: bool = True) -> dict[str, list[str]]:
    """Build the ticker universe from index constituents.

    Strategy:
    1. Try cache first (fresh within 24h)
    2. If no cache or force_refresh:
       a. Scrape Wikipedia for each region's index pages
       b. Collect unique tickers per region
       c. Save to cache
       d. Return dict of {region: sorted_unique_tickers}
    3. write_cache=False (MC 10264 F2): run entirely in memory — the tracked
       universe_cache.json / index_constituents.json are NOT written. The
       guarded refresh CLI (scripts/refresh_universe.py) is the ONLY writer
       of the tracked registry, behind its own guard.

    Returns dict[str, list[str]] with keys:
        usa, sweden, uk, germany, france, japan,
        hongkong, china, india, australia, canada,
        switzerland, korea, international
    """
    # Try cache first
    if not force_refresh:
        cache = _read_cache()
        if cache and _is_cache_fresh(cache):
            logger.debug("Using cached universe data")
            return cache["tickers"]

    logger.info("Building universe from CSV/XLSX sources + Wikipedia fallback")
    universe: dict[str, list[str]] = {}
    constituents_cache: dict[str, list[str]] = {}

    # --- USA: load from verified CSV (no Wikipedia scraping) ---
    logger.info("Fetching USA tickers from curated CSV source...")
    us_tickers = _load_us_tickers_from_csv()
    if us_tickers:
        universe["usa"] = sorted(us_tickers)
        constituents_cache["usa/csv"] = universe["usa"]
        logger.info(f"  usa: {len(universe['usa'])} tickers (from CSV)")
    else:
        logger.warning("  usa: no tickers from CSV — will fall back to embedded data")

    # --- Load from local CSV/XLSX sources (verified, structured data) ---
    for region_key, source in LOCAL_TICKER_SOURCES.items():
        logger.info(f"Fetching {region_key} tickers from local source...")
        tickers = _load_tickers_from_local_source(source)
        if tickers:
            universe[region_key] = tickers
            constituents_cache[f"{region_key}/local"] = tickers
            logger.info(f"  {region_key}: {len(tickers)} tickers (from local {source['type']})")
        else:
            logger.warning(f"  {region_key}: no tickers from local source — will use Wikipedia/fallback")

    # --- Per-region scraping for remaining regions (Wikipedia fallback) ---
    for region_key, pages in WIKI_PAGES.items():
        if region_key in universe:
            # Already loaded from local source — skip Wikipedia for this region
            logger.info(f"Skipping {region_key} (already loaded from CSV/XLSX)")
            continue

        logger.info(f"Fetching {region_key} index constituents...")
        region_tickers: set[str] = set()

        for page_name, description in pages:
            logger.info(f"  Fetching {region_key}: {description} ({page_name})...")
            tickers = _extract_tickers_from_wikipedia(page_name)
            if tickers:
                logger.info(f"    Got {len(tickers)} tickers from {description}")
                region_tickers.update(tickers)
                key = f"{region_key}/{description}"
                constituents_cache[key] = tickers
            else:
                logger.warning(f"    No tickers from {description} ({page_name})")

        if region_tickers:
            if len(region_tickers) < 20:
                logger.info(f"  {region_key}: only {len(region_tickers)} tickers from Wikipedia — skipping (will use embedded fallback)")
            else:
                universe[region_key] = sorted(region_tickers)
                logger.info(f"  {region_key}: {len(universe[region_key])} tickers")
        else:
            logger.warning(f"  {region_key}: no tickers found — will fall back to embedded data")

    # --- Fill missing/deficient regions with embedded fallback data ---
    fallback = _build_embedded_fallback()
    for region_key in fallback:
        if region_key not in universe or len(universe[region_key]) < 5:
            if region_key in universe:
                del universe[region_key]
            universe[region_key] = list(fallback[region_key])
            logger.info(f"  {region_key}: {len(universe[region_key])} tickers (embedded fallback)")

    # --- M4/M5: the generated regions are AUTHORITATIVE, never overridden ---
    # The WIKI/local scraping above can leave a partially-scraped region (e.g. a
    # 20..80-symbol Wikipedia table for sweden) that would otherwise shadow the
    # full reshaped list from the M1/M2 raw files. DESIGN C1/I1 pins the reshaped
    # region lists as the source for these M4-managed regions, so they must win
    # unconditionally (a partial scrape is the exact bare-number/junk defect the
    # chain exists to remove — PLAN §1). USA/UK/AU/CA/CH stay untouched.
    _MANAGED = {"sweden", "norway", "denmark", "finland", "germany", "france",
                "japan", "hongkong", "china", "india", "korea"}
    for _reg in list(universe):
        if _reg in _MANAGED:
            universe[_reg] = list(fallback[_reg]) if _reg in fallback else []
    for _reg in _MANAGED:  # ensure every managed region key exists in the cache
        if _reg not in universe and _reg in fallback:
            universe[_reg] = list(fallback[_reg])

    # --- MC 10342: region Yahoo-suffix hygiene (THE choke point) -----------
    # Every non-US bucket now leaves the builder REGION_META-primary-suffixed
    # (usa/international carry primary None -> pass through). The fetch path
    # (bulk_fetcher) sends registry symbols VERBATIM to yfinance, so without
    # this step the next genuine rebuild regenerates suffix-less uk/au/ca and
    # wrong-suffix Swiss forms and SILENTLY REVERTS the T25 registry hygiene
    # (761e26e). Rules + T25-form evidence: universe_suffix module docstring.
    universe = {r: apply_region_suffix(r, v) for r, v in universe.items()}

    # --- Backward-compatibility: international = all non-US regions combined ---
    intl = set()
    for region_key in WIKI_PAGES:
        intl.update(universe.get(region_key, []))
    # include the M4 Nordic/Europe generated regions in the non-US aggregate too,
    # so `international` reflects the full reshaped universe (norway/denmark/
    # finland are not WIKI_PAGES keys).
    for _reg in _MANAGED:
        intl.update(universe.get(_reg, []))
    universe["international"] = sorted(intl)
    logger.info(f"  International (combined non-US): {len(universe['international'])} tickers")

    # Save to both caches — or, for in-memory builds (write_cache=False,
    # MC 10264 F2), touch nothing on disk: the guarded CLI owns that write.
    if write_cache:
        _write_cache(universe)
        _save_index_constituents(constituents_cache)

    total = {k: len(v) for k, v in universe.items()}
    logger.info(f"Universe built: {total}")
    return universe


# Public API
# ---------------------------------------------------------------------------

_universe_cache: Optional[dict[str, list[str]]] = None


def get_universe(region: str | None = None, force_refresh: bool = False,
                 write_cache: bool = True) -> list[str] | dict[str, list[str]]:
    """Get ticker universe.

    Args:
        region: Region key or None for all.
        force_refresh: If True, skip cache and fetch live data.
        write_cache: If False, the (force) build is kept in memory and the
            tracked cache files are left untouched (MC 10264 F2; the guarded
            refresh CLI passes False).

    Returns:
        If region is specified, returns list[str] of tickers for that region.
        If region is None, returns dict[str, list[str]] of all regions.
    """
    global _universe_cache
    if _universe_cache is None or force_refresh:
        _universe_cache = _build_universe(force_refresh, write_cache)

    if region is None:
        return dict(_universe_cache)

    return list(_universe_cache.get(region, []))


def get_universe_count() -> dict[str, int]:
    """Return dict of region -> number of tickers (from cache)."""
    global _universe_cache
    if _universe_cache is None:
        # Load from cache file directly without building
        cache = _read_cache()
        if cache and "tickers" in cache:
            _universe_cache = cache["tickers"]

    if _universe_cache is None:
        # Fallback: build without scraping (use embedded data only)
        _universe_cache = _build_embedded_fallback()

    return {region: len(tickers) for region, tickers in _universe_cache.items()}


def get_all_tickers() -> list[str]:
    """Return all unique tickers across all regions."""
    universe = get_universe()
    all_tickers = set()
    for tickers in universe.values():
        all_tickers.update(tickers)
    return sorted(all_tickers)


def get_all_universes() -> list[str]:
    """Alias for get_all_tickers — return all tickers across all regions."""
    return get_all_tickers()


def get_region_count() -> dict[str, int]:
    """Alias for get_universe_count."""
    return get_universe_count()


def get_total_count() -> int:
    """Return total number of unique tickers across all regions."""
    return len(get_all_tickers())


def _load_generated_region_lists() -> dict[str, list[str]]:
    """Load the M4-managed region lists from the reconciled manifest (D4) and,
    for any region absent there (currently only norway), reconcile the raw file
    via the extended normalizer (symbols.SymbolNormalizer + the M3 rule table).

    Returns dict region_key -> list of canonical Yahoo tickers, each member of
    DOD_SUFFIXES. This is the M4 replacement for the hardcoded sweden/thin
    __TICKERS literals (I1). The normalizer / rule table is reused, not forked
    (I2, I6). See DESIGN C1, C1b, D4; card 947.5.
    """
    import sys as _sys
    from pathlib import Path as _Path

    _DATA_DIR = _Path("/srv/workspace/rocket-region-universe/data")
    _MANIFEST = _DATA_DIR / "reconciled_manifest.json"

    # ---- reuse the M3 rule table + reconcile driver, do not re-implement ----
    # (coding-discipline / reuse-research-gate STEP 0: the reconcile logic is
    #  the sibling module reconcile_m3.py, not something to rewrite here.)
    _NORWAY_FILE = _DATA_DIR / "norway_raw.txt"
    if str(_DATA_DIR) not in _sys.path:
        _sys.path.insert(0, str(_DATA_DIR))
    from reconcile_m3 import (  # noqa: E402
        reconcile,
        load_raw_rows,
        _parse_nordic_lines,
    )

    lists: dict[str, list[str]] = {}

    # (a) regions present in the manifest -> take already-canonical ok yahoo.
    if _MANIFEST.exists():
        manifest = json.loads(_MANIFEST.read_text())
        by_region: dict[str, list[str]] = {}
        for row in manifest:
            if row["status"] == "ok" and row["yahoo"]:
                by_region.setdefault(row["region"], []).append(row["yahoo"])
        for reg, syms in by_region.items():
            lists[reg] = sorted(set(syms))

    # (b) M4-managed Nordic/Europe regions that M3's manifest does NOT cover.
    #     As of this card that is only NORWAY: norway_raw.txt landed after M3's
    #     manifest was written, so reconcile_m3.load_raw_rows() ignores it. Reuse
    #     the SAME reconcile driver + Nordic TSV parser (nothing re-implemented).
    managed_raw = {"sweden", "norway", "denmark", "finland", "germany", "france"}
    raw_rows: list[tuple[str, str]] = []
    # every managed region already streamed by reconcile_m3.load_raw_rows()
    for sym, region in load_raw_rows():
        if region in managed_raw:
            raw_rows.append((sym, region))
    # norway (absent from reconcile_m3.load_raw_rows): same TSV format
    if _NORWAY_FILE.exists():
        for sym in _parse_nordic_lines(_NORWAY_FILE.open()):
            raw_rows.append((sym, "norway"))

    reconciled = reconcile(raw_rows)
    for row in reconciled:
        if row["status"] == "ok" and row["yahoo"] and row["region"] in managed_raw:
            lists.setdefault(row["region"], []).append(row["yahoo"])
    for reg in managed_raw:
        if reg in lists:
            lists[reg] = sorted(set(lists[reg]))

    # (c) harden: sanitize every generated symbol against the DoD regex.
    dod_re = re.compile(r"^[A-Z0-9.\-]+\.(ST|OL|CO|HE|PA|DE|T|HK|KS|NS|BO)$")
    clean: dict[str, list[str]] = {}
    for reg, syms in lists.items():
        clean_syms = sorted({s for s in syms if dod_re.match(s)})
        if clean_syms:
            clean[reg] = clean_syms
    return clean


def _build_embedded_fallback() -> dict[str, list[str]]:
    """Fallback universe builder with embedded lists if Wikipedia fails.

    This provides ~1,200+ tickers as a minimum viable universe,
    covering all regions instead of just usa + international.

    The sweden/thin regions (M4 scope) are GENERATED from the M1/M2 raw files
    via the extended normalizer -- NOT hardcoded (I1). USA/UK/AU/CA/CH remain
    untouched embedded lists (out of M4 scope).
    """
    # USA — S&P 500 (core holdings) + major tech
    USA_TICKERS = [
        # S&P 500 core
        "MMM", "ABT", "AOS", "ABNB", "ADM", "ADP", "ADSK", "AAP", "AEP", "AES",
        "AFL", "A", "APD", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL",
        "GOOG", "GOOGL", "MO", "AMZN", "AAL", "AEP", "AXP", "AIG", "AMT",
        "AWK", "AMP", "ABC", "AME", "AON", "APA", "AAPL", "AMAT", "APTV",
        "ACN", "ADBE", "AMD", "AFRM", "AES", "AES", "AZEK", "ABBV", "ABC",
        "ABMD", "ABT", "ACGL", "ACN", "ADP", "ADSK", "AEE", "AEP", "AES",
        "AFL", "A", "APD", "AR", "AOS", "APA", "AVGO", "AWK", "AXON",
        "BALL", "BAX", "BDX", "BEN", "BF.B", "BK", "BKNG", "BIO", "BIIB",
        "BLK", "BMY", "BRK.B", "BR", "BSX", "BWA", "BX", "BXP", "CAG",
        "CRL", "C", "COO", "CPB", "COP", "CRM", "CSCO", "CSGP", "CSX",
        "CTAS", "CTLT", "CAT", "CBOE", "CBRE", "CDNS", "CDAY", "CE", "CDNS",
        "CHRW", "CPT", "CPB", "CME", "CMS", "COST", "CCI", "CTRA", "CUZ",
        "CRWD", "CMI", "CVS", "DHR", "DRI", "DVA", "DAY", "DE", "DAL",
        "DVN", "DXCM", "DECK", "DLR", "DFS", "DG", "DLTR", "EBAY", "ECL",
        "EFX", "EIX", "EMN", "EMR", "ENPH", "EOG", "EPAM", "EQIX", "EQR",
        "ESS", "ETN", "EBAY", "EXC", "EXPE", "EXPD", "EXR", "XOM", "FFIV",
        "FDS", "FICO", "FAST", "FRT", "FDX", "FIS", "FISV", "FITB", "F",
        "GPC", "GILD", "GL", "GPN", "GS", "GDDY", "HAL", "HIG", "HAS",
        "HRL", "HST", "HWM", "HP", "HOLX", "HD", "HON", "HPE", "HLT",
        "HF", "HOLX", "HSY", "HUM", "IBM", "IP", "IPG", "IFF", "IR",
        "IDXX", "IT", "ICE", "INVH", "IQV", "IRM", "JBHT", "JBL", "JKHY",
        "JNJ", "JCI", "JPM", "JPM", "KMB", "KIM", "KMI", "KLAC", "KHC",
        "KR", "LHX", "LH", "LRCX", "LVS", "LDOS", "LEN", "LIN", "LYV",
        "LKQ", "LLY", "LMT", "LULU", "LUK", "LYB", "MCD", "MCHP", "MU",
        "MSFT", "MRO", "MPC", "MKTX", "MAR", "MLM", "MAS", "MA", "MKC",
        "MCD", "MCO", "MS", "MOS", "MSI", "MMC", "MDLZ", "MPWR", "MNST",
        "MCO", "MCHP", "MRVL", "MRK", "META", "MO", "MPC", "MNST", "MDLZ",
        "MTCH", "MFN", "MTD", "MCK", "MUR", "NCLH", "NDAQ", "NEE", "NEM",
        "NDSN", "NSC", "NTRS", "NOC", "NUE", "NVDA", "NVR", "NXPI", "ORLY",
        "OXY", "ODFL", "OMC", "ON", "ORCL", "OTIS", "PCG", "PCI", "PEG",
        "PEP", "PFE", "PCG", "PM", "PSA", "PH", "PAYC", "PYPL", "PNR",
        "PNC", "POOL", "PPG", "PPL", "PFG", "PG", "PGR", "PLTR", "PMD",
        "PWR", "QCOM", "DGX", "RSG", "RAI", "RMD", "RVTY", "RBA", "RBLX",
        "RTX", "O", "REG", "REGN", "RF", "RHI", "ROK", "ROL", "ROP",
        "ROST", "RCL", "RGEN", "SPGI", "RSK", "SLB", "STX", "SRE", "NOW",
        "SBAC", "SHW", "SPG", "SWKS", "SJM", "SNPS", "SNA", "SO", "LUV",
        "SPLK", "SPGI", "STE", "SYF", "SYK", "SMCI", "TGT", "TSLA", "TEL",
        "TDY", "TFX", "TER", "TSLA", "TPR", "TRGP", "TGT", "TROW", "TTWO",
        "TT", "TJX", "TSCO", "TXN", "TXT", "TMO", "TJX", "TMO", "TFC",
        "USB", "UHS", "ULTA", "UNP", "UAL", "UPS", "URI", "UNH", "U",
        "VLO", "VTR", "VLTO", "VRSN", "VRSK", "VZ", "VRTX", "VFC", "VIAC",
        "VTRS", "V", "WAB", "WBA", "WMT", "DIS", "WM", "WAT", "WEC",
        "WFC", "WLK", "WDAY", "WRB", "WRK", "WST", "WDC", "WU", "WRB",
        "WRK", "WTW", "WY", "WYNN", "XEL", "XYL", "YUM", "ZBRA", "ZTS",
        "ZM", "ZS", "ZM", "ZS", "ZS", "ZS",
    ]

    # UK
    UK_TICKERS = [
        "BP.L", "SHEL.L", "GSK.L", "AZN.L", "ULVR.L", "DGE.L",
        "BARC.L", "HSBA.L", "LLOY.L", "VOD.L", "GLEN.L", "RIO.L",
        "AAL.L", "BAT.L", "REL.L", "RR.L", "BA.L", "NG.L",
        "LSEG.L", "ANTO.L", "DGE.L", "EXPN.L", "ITRK.L",
        "STAN.L", "MKS.L", "BDEV.L", "PRU.L", "AVST.L",
        "BLND.L", "CRDA.L", "CPG.L", "DCC.L", "EZJ.L",
        "FRES.L", "HWDN.L", "III.L", "IHG.L", "IMB.L",
        "ITRK.L", "JD.L", "KGF.L", "LAND.L", "LAND.L",
        "MNDI.L", "MNG.L", "NWG.L", "OCDO.L", "PSH.L",
        "RKT.L", "SGE.L", "SN.L", "SMDS.L", "SMIN.L",
        "SDR.L", "TSCO.L", "WTB.L", "WEIR.L", "WN.L",
    ]

    # France
    # (france list now GENERATED from france_raw.txt via normalizer — I1)

    # Japan (Nikkei 225 + major TSE stocks)
    # (japan list now GENERATED from asia_raw.txt via normalizer — I1)

    # Hong Kong (Hang Seng constituents)
    # (hongkong list now GENERATED from asia_raw.txt via normalizer — I1)

    # China (A-shares: Shanghai SS + Shenzhen SZ + HK-listed Chinese names)
    # (china list now GENERATED from asia_raw.txt via normalizer — I1)

    # India
    # (india list now GENERATED from asia_raw.txt via normalizer — I1)

    # Australia
    AUSTRALIA_TICKERS = [
        "BHP.AX", "CBA.AX", "RIO.AX", "CSL.AX", "NAB.AX",
        "ANZ.AX", "WES.AX", "WBC.AX", "FMG.AX", "MQG.AX",
        "TLS.AX", "TCL.AX", "WOW.AX", "GMG.AX", "COL.AX",
        "STO.AX", "S32.AX", "WDH.AX", "REA.AX", "QBE.AX",
        "APP.AX", "SUN.AX", "WDS.AX", "EML.AX", "ASX.AX",
    ]

    # Canada
    CANADA_TICKERS = [
        "SHOP.TO", "RY.TO", "TD.TO", "BNJ.TO", "BNS.TO",
        "CNR.TO", "CP.TO", "ENB.TO", "SU.TO", "TRP.TO",
        "CNQ.TO", "BMO.TO", "CM.TO", "ABX.TO", "K.TO",
        "FCX.TO", "CVE.TO", "IMO.TO", "TRI.TO", "MFC.TO",
        "WCN.TO", "FTS.TO", "HEI.TO", "GIB.A", "QSR.TO",
    ]

    # Switzerland (SMI + major SIX stocks)
    SWITZERLAND_TICKERS = [
        "NESN.SZ", "ROG.SZ", "NOVN.SZ", "ABBN.SZ", "GIVN.SZ",
        "ZURN.SZ", "SREN.SZ", "LONN.SZ", "2000.SN", "EKBAS.SZ",
        "VONN.SZ", "SNGY.SZ", "LISN.SZ", "PAXN.SZ", "BMOG.SZ",
        "SNCA.SZ", "SIKA.SZ", "SCMN.SZ", "UBSG.SZ", "CSGN.SZ",
        "GIGN.SZ", "KNIN.SZ", "AENS.SZ", "ANEA.SZ", "ASYL.SZ",
        "EVTN.SZ", "HOLN.SZ", "KSNR.SZ", "MILL.SZ", "MIGN.SZ",
        "PFBI.SZ", "SIAN.SZ", "TELSS.SZ", "ZILN.SZ", "ALGN.SZ",
    ]

    # South Korea (KOSPI + KOSDAQ major)
    # (korea list now GENERATED from asia_raw.txt via normalizer — I1)

    generated = _load_generated_region_lists()

    universe = {
        "usa": sorted(set(USA_TICKERS)),
        "uk": sorted(set(UK_TICKERS)),
        "australia": sorted(set(AUSTRALIA_TICKERS)),
        "canada": sorted(set(CANADA_TICKERS)),
        "switzerland": sorted(set(SWITZERLAND_TICKERS)),
        # M4-generated regions (from raw files via normalizer) replace the
        # hardcoded sweden/thin literals (I1). Each is DoD-sanitized.
        **{reg: sorted(set(generated.get(reg, [])))
           for reg in ("sweden", "norway", "denmark", "finland",
                       "germany", "france", "japan", "hongkong",
                       "china", "india", "korea")},
    }
    # MC 10342: same suffix hygiene as the scrape path — the Swiss literals
    # above carry pre-Yahoo .SZ/.SN tails, and direct callers of this
    # function (get_universe_count) must never see them un-suffixed.
    universe = {r: apply_region_suffix(r, v) for r, v in universe.items()}
    # international = all non-usa regions combined (backward compatible),
    # DERIVED from the suffixed buckets (MC 10342): every member is
    # resolve-shaped, so a rebuild stops contributing stale pre-suffix
    # drop-forms to the okontrollerat pool.
    universe["international"] = sorted(set().union(
        *(v for k, v in universe.items() if k not in ("usa", "international"))))
    return universe
