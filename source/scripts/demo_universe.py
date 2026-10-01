"""Shared universe constants for the demo-page pipeline (MC 3874).

Moved verbatim out of generate_demo_page.py so the generator and the
indicator-eval side share ONE universe and ONE cache-naming rule (seam C4).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Demo universe: bounded per the hosting decision (owner dropped the 25k
# rebuild). One region key per served tab.
UNIVERSE: dict[str, list[str]] = {
    "usa": ["MSFT", "DIS", "WMT", "NVDA", "XOM", "CSCO", "AMZN", "MA", "BAC",
            "JPM", "CRM", "HD", "PG", "ADBE", "JNJ", "V", "AAPL", "META",
            "GOOGL", "UNH"],
    "sweden": ["SAAB-B.ST", "ATCO-A.ST", "SEB-A.ST", "ASSA-B.ST", "SWED-A.ST",
               "ERIC-B.ST"],
    "germany": ["SAP.DE", "DBK.DE", "ALV.DE", "SIE.DE", "DTE.DE", "BMW.DE",
                "BAS.DE", "VOW3.DE", "IFX.DE"],
}
REGION_LABELS = {"usa": "USA", "sweden": "Sverige", "germany": "Tyskland"}
CACHE_DIR = REPO_ROOT / "source" / "data" / "raw"


def cache_filename(ticker: str) -> str:
    """Seam C4: ticker -> cache CSV stem; the runner CLI derives stems alike."""
    return ticker.replace(".", "_").replace("-", "_") + ".csv"
