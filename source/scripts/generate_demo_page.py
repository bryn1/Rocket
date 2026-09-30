#!/usr/bin/env python3
"""Generate the static demo page (index.html) from freshly scored data.

Reuses the exact scoring path of the Dash app (app.py:
_compute_all_indicators + _score_from_summary) so the served page always
matches the engine. Fetches OHLCV per ticker (yfinance), scores it,
renders a self-contained page with one tab per region plus a text filter,
and (unless --dry-run) commits and pushes to the bryn1 mirror.

Usage:
    python3 scripts/generate_demo_page.py            # fetch, render, publish
    python3 scripts/generate_demo_page.py --dry-run  # render only, no git
    python3 scripts/generate_demo_page.py --skip-fetch  # reuse cached CSVs
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "source"))

from rocket.data.fetcher import fetch_ohlcv  # noqa: E402

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

STYLE = """<style>
body{background:#0d0d1a;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{color:#00e676;margin:0}h2{color:#00e676;border-bottom:1px solid #2a2a3e;padding-bottom:6px}
.sub{color:#9e9e9e;font-size:14px;margin:6px 0 18px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tab-btn{background:#1a1a2e;border:1px solid #2a2a3e;color:#e0e0e0;padding:8px 18px;border-radius:6px;cursor:pointer;font-size:14px}
.tab-btn.active{background:#00e67622;border-color:#00e676;color:#00e676}
table{width:100%;border-collapse:collapse;background:#1a1a2e;border-radius:8px;overflow:hidden}
th{background:#12122a;color:#9e9e9e;text-align:left;padding:10px 12px;font-size:13px}
td{padding:9px 12px;border-top:1px solid #2a2a3e;font-size:14px}
tr:hover td{background:#22223c}
.buy{color:#00e676;font-weight:bold}.sell{color:#ef5350;font-weight:bold}.hold{color:#ffb74d}
.score-hi{color:#00e676;font-weight:bold}.score-mid{color:#ffb74d;font-weight:bold}.score-lo{color:#ef5350;font-weight:bold}
input[type=text]{background:#1a1a2e;border:1px solid #2a2a3e;color:#e0e0e0;padding:8px 12px;border-radius:6px;width:220px;font-size:14px}
.footer{color:#616161;font-size:12px;margin-top:22px}
.region-note{color:#9e9e9e;font-size:13px;margin:4px 0 10px}
</style>"""

JS = """<script>
function showRegion(id, btn){
  document.querySelectorAll('.region-panel').forEach(p=>p.style.display='none');
  document.getElementById('panel-'+id).style.display='block';
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
}
function filterTable(input, panelId){
  const q = input.value.toUpperCase();
  document.querySelectorAll('#'+panelId+' tbody tr').forEach(tr=>{
    tr.style.display = tr.cells[0].textContent.toUpperCase().includes(q) ? '' : 'none';
  });
}
</script>"""


_LEGACY_REGION = {"usa": "usa", "sweden": "sweden", "germany": "germany"}


def _score_df(ticker: str, df: pd.DataFrame, region: str) -> dict | None:
    """Score one OHLCV frame via the app's scoring path."""
    from app import _compute_all_indicators, _score_from_summary
    summary, _ = _compute_all_indicators(df)
    scored = _score_from_summary(summary, ticker=ticker, region=_LEGACY_REGION[region])
    rs = scored["rocket_score"]
    return {
        "ticker": ticker,
        "signal": summary.signal if hasattr(summary, "signal") else
                  ("BUY" if summary.buy_count > summary.sell_count else
                   "SELL" if summary.sell_count > summary.buy_count else "HOLD"),
        "overall": rs.overall_score,
        "momentum": rs.momentum_score,
        "trend": rs.trend_score,
        "volatility": rs.volatility_score,
        "volume": rs.volume_score,
        "close": round(float(df["close"].iloc[-1]), 2),
    }


def score_ticker(ticker: str, region: str, skip_fetch: bool) -> dict | None:
    """Fetch OHLCV and score one ticker via the app's scoring path."""
    csv_path = CACHE_DIR / f"{ticker.replace('.', '_').replace('-', '_')}.csv"
    df = None
    if skip_fetch and csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["date"])
    if df is None:
        fetched = fetch_ohlcv([ticker], period="1y")
        df = fetched.get(ticker)
        if df is not None and len(df) > 10:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(csv_path, index=False)
    if df is None or len(df) < 20:
        return None
    return _score_df(ticker, df, region)


def _signal_class(signal: str) -> str:
    return {"BUY": "buy", "SELL": "sell"}.get(signal, "hold")


def _score_class(score: float) -> str:
    return "score-hi" if score >= 70 else "score-mid" if score >= 40 else "score-lo"


def _table(rows: list[dict], panel_id: str) -> str:
    parts = [f'<div class="region-panel" id="panel-{panel_id}">',
             '<div class="table-wrap"><table><thead><tr>',
             '<th>Ticker</th><th>Signal</th><th>Overall</th><th>Momentum</th>',
             '<th>Trend</th><th>Volatility</th><th>Volume</th><th>Close</th>',
             '</tr></thead><tbody>']
    for r in rows:
        parts.append(
            f'<tr><td>{r["ticker"]}</td>'
            f'<td class="{_signal_class(r["signal"])}">{r["signal"]}</td>'
            f'<td class="{_score_class(r["overall"])}">{r["overall"]:.1f}</td>'
            f'<td>{r["momentum"]:.1f}</td><td>{r["trend"]:.1f}</td>'
            f'<td>{r["volatility"]:.1f}</td><td>{r["volume"]:.1f}</td>'
            f'<td>{r["close"]}</td></tr>')
    parts.append('</tbody></table></div></div>')
    return "\n".join(parts)


def render(results: dict[str, list[dict]], as_of: str) -> str:
    all_rows = sorted((r for rows in results.values() for r in rows),
                      key=lambda r: r["overall"], reverse=True)
    top10 = all_rows[:10]
    n = len(all_rows)
    regions = list(results.keys())

    tabs = ['<div class="tabs" role="tablist">']
    tabs.append(f'<button class="tab-btn active" onclick="showRegion(\'all\',this)">Alla ({n})</button>')
    for reg in regions:
        tabs.append(f'<button class="tab-btn" onclick="showRegion(\'{reg}\',this)">'
                    f'{REGION_LABELS.get(reg, reg)} ({len(results[reg])})</button>')
    tabs.append('</div>')

    panels = [_table(all_rows, "all")]
    for reg in regions:
        panels.append(_table(results[reg], reg))

    top_rows = "".join(
        f'<tr><td>{r["ticker"]}</td>'
        f'<td class="{_signal_class(r["signal"])}">{r["signal"]}</td>'
        f'<td class="{_score_class(r["overall"])}">{r["overall"]:.1f}</td></tr>'
        for r in top10)

    universe_lines = "".join(
        f'<div class="region-note"><b>{REGION_LABELS.get(reg, reg)}</b> ({len(rows)}): '
        f'{", ".join(r["ticker"] for r in rows)}</div>'
        for reg, rows in results.items())

    return f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rocket — Stock Scanner Demo</title>{STYLE}</head>
<body><div class="wrap">
<header class="page"><h1>Rocket — Stock Scanner Demo</h1>
<div class="sub">Läs-only demo · {n} tickers i {len(regions)} regioner · scorerade med Rocket-score · data hämtad {as_of}</div></header>
<h2>Rankings</h2>
<div style="margin-bottom:10px"><input type="text" placeholder="Filtrera ticker…" oninput="filterTable(this,'panel-all')"></div>
{''.join(tabs)}
{''.join(panels)}
<h2>Top 10 — köpsvy</h2>
<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Signal</th><th>Overall</th></tr></thead>
<tbody>{top_rows}</tbody></table></div>
<h2>Universe</h2>
{universe_lines}
<div class="footer">Genererad {as_of} av scripts/generate_demo_page.py — data hämtas och sidan publiceras dagligen (systemd-timer rocket-demo-publish).</div>
</div>{JS}</body></html>"""


def publish() -> None:
    # Sync with the mirror first: a remote that moved (e.g. the hosting
    # reconciler) must not turn the nightly push into a rejection.
    subprocess.run(["git", "pull", "--rebase", "bryn1", "main"], cwd=REPO_ROOT, check=False)
    subprocess.run(["git", "add", "index.html"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "-c", "user.name=code (MC 3848)", "-c",
         "user.email=code@agent-town.local", "commit", "-m",
         "publish: regenerate demo page from fresh daily scan (MC 3848)"],
        cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "bryn1", "HEAD:main"], cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="render, no git push")
    parser.add_argument("--skip-fetch", action="store_true", help="reuse cached CSVs")
    args = parser.parse_args()

    results: dict[str, list[dict]] = {}
    errors: list[str] = []
    for region, tickers in UNIVERSE.items():
        rows = []
        for ticker in tickers:
            try:
                row = score_ticker(ticker, region, args.skip_fetch)
                if row:
                    rows.append(row)
                else:
                    errors.append(f"{ticker}: no data")
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the page
                errors.append(f"{ticker}: {exc}")
        results[region] = rows

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render(results, as_of)
    (REPO_ROOT / "index.html").write_text(html, encoding="utf-8")
    print(f"Rendered index.html: {sum(len(v) for v in results.values())} tickers, "
          f"{len(errors)} errors: {errors}")
    if not args.dry_run:
        publish()
        print("Published to bryn1 main.")
    else:
        print("Dry run — not published.")


if __name__ == "__main__":
    main()
