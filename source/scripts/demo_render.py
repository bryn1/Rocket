"""HTML renderers for the daily demo page (MC 3874): pure frame-data -> HTML.

Holds STYLE/JS, the per-region score tables, the new Indikatorer tab (seam
C3: renders only the pinned indicator_stats.json fields) and the freshness
banner. No I/O, no time reads: staleness is computed from explicit args so
render is deterministic for fixed inputs.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from demo_universe import REGION_LABELS

# Guard markers (generate_demo_page._guard asserts these exist).
BANNER_MARKER = 'id="freshness"'
INDICATOR_TAB_MARKER = 'id="tab-indicators"'
PANEL_PREFIX = 'id="panel-'

EMPTY_BACKTEST_TEXT = "Backtest saknas — publiceras när närmaste löpning lyckats"

STYLE = """<style>
body{background:#0d0d1a;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{color:#00e676;margin:0}h2{color:#00e676;border-bottom:1px solid #2a2a3e;padding-bottom:6px}
.sub{color:#9e9e9e;font-size:14px;margin:6px 0 18px}
.sub.stale{color:#ffb74d;font-weight:bold}
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
.footer a{color:#00e676}
.region-note{color:#9e9e9e;font-size:13px;margin:4px 0 10px}
.ind-note{color:#ffb74d;font-size:13px;margin:6px 0 10px}
.risk-badge{background:#ffb74d22;color:#ffb74d;border:1px solid #ffb74d;border-radius:4px;padding:1px 6px;font-size:11px;margin-left:6px}
.ev-strong{color:#00e676;font-weight:bold}
.ev-moderate{color:#ffb74d;font-weight:bold}
.ev-weak{color:#e0e0e0}
.ev-none{color:#ef5350;font-weight:bold}
.ev-insufficient{color:#9e9e9e}
.ev-error{color:#ef5350;font-weight:bold;text-decoration:underline}
.calc-err{color:#ef5350;font-size:12px}
</style>"""

JS = """<script>
function showRegion(id, btn){
  document.querySelectorAll('.region-panel').forEach(p=>p.style.display='none');
  document.getElementById('panel-'+id).style.display='block';
  document.querySelectorAll('.tab-btn').forEach(b=>{
    b.classList.remove('active'); b.setAttribute('aria-selected','false');});
  btn.classList.add('active');
  btn.setAttribute('aria-selected','true');
}
function filterTable(input, panelId){
  const q = input.value.toUpperCase();
  document.querySelectorAll('#'+panelId+' tbody tr').forEach(tr=>{
    tr.style.display = tr.cells[0].textContent.toUpperCase().includes(q) ? '' : 'none';
  });
}
</script>"""

_EV_RANK = {"strong": 0, "moderate": 1, "weak": 2, "none": 3,
            "insufficient": 4, "error": 5}
_EV_LABEL = {"strong": "stark", "moderate": "måttlig", "weak": "svag",
             "none": "ingen", "insufficient": "otillräcklig", "error": "fel"}


def _signal_class(signal: str) -> str:
    return {"BUY": "buy", "SELL": "sell"}.get(signal, "hold")


def _score_class(score: float) -> str:
    return "score-hi" if score >= 70 else "score-mid" if score >= 40 else "score-lo"


def _num(v):
    """True for values usable in formatting (not None, not NaN)."""
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _pct(v) -> str:
    return f"{100 * v:.1f}%" if _num(v) else "—"


def _netpct(v) -> str:
    return f"{v:+.2f}%" if _num(v) else "—"


def is_stale(run_iso: str, data_last_bar: str) -> bool:
    """Freshness rule §5.5: run ts > 26 h newer than the newest bar = stale."""
    if not data_last_bar:
        return False
    try:
        run = datetime.strptime(run_iso[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc)
        bar = datetime.strptime(data_last_bar[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return False
    return (run - bar) > timedelta(hours=26)


def _table(rows: list[dict], panel_id: str) -> str:
    parts = [f'<div class="region-panel" id="panel-{panel_id}" role="tabpanel">',
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


def _indicator_row(key: str, ind: dict, h: int) -> str:
    hz = (ind.get("horizons") or {}).get(str(h), {})
    long_blk, short_blk = hz.get("long") or {}, hz.get("short") or {}
    weight = ind.get("evidence_weight") or "insufficient"
    css = f"ev-{weight}" if weight in _EV_RANK else "ev-insufficient"
    label = _EV_LABEL.get(weight, weight)
    name = ind.get("name") or key
    badge = '<span class="risk-badge">risk</span>' if ind.get("risk_only") else ""
    signals = f'{ind.get("n_buy", 0)} / {ind.get("n_sell", 0)}'
    errors = ind.get("calc_errors") or 0
    if errors:
        signals += f' <span class="calc-err">beräkningsfel: {errors}</span>'
    return (
        f'<tr><td>{name}{badge}</td><td>{ind.get("category", "")}</td>'
        f'<td>{signals}</td>'
        f'<td>{_pct(long_blk.get("hit_rate"))}</td>'
        f'<td>{_netpct(long_blk.get("avg_net_pct"))}</td>'
        f'<td>{_pct(short_blk.get("hit_rate"))}</td>'
        f'<td class="{css}">{label}</td></tr>')


def render_indikatorer_panel(doc: dict | None, note: str | None) -> str:
    """Seam C3: renders ONLY pinned artifact fields; no doc -> honest text."""
    parts = ['<div class="region-panel" id="panel-indicators" role="tabpanel" '
             'style="display:none">',
             '<h2>Indikatorer — backtest</h2>']
    if note:
        parts.append(f'<div class="ind-note">{note}</div>')
    if not doc or not doc.get("indicators"):
        parts.append(f'<div class="ind-note">{EMPTY_BACKTEST_TEXT}</div></div>')
        return "\n".join(parts)
    h = doc.get("primary_horizon_days", 10)
    order = sorted(doc["indicators"].items(),
                   key=lambda kv: (_EV_RANK.get(
                       (kv[1] or {}).get("evidence_weight"), 9), kv[0]))
    parts.append(
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Namn</th><th>Kategori</th><th>Köp/Sälj-signaler</th>'
        f'<th>Hit-rate ({h}d)</th><th>Net % ({h}d)</th>'
        '<th>Short hit-rate</th><th>Evidence-vikt</th>'
        '</tr></thead><tbody>')
    for key, ind in order:
        parts.append(_indicator_row(key, ind or {}, h))
    parts.append('</tbody></table></div></div>')
    return "\n".join(parts)


def render(results: dict[str, list[dict]], run_ts: str, data_last_bar: str,
           indicators: dict | None = None, indicators_note: str | None = None) -> str:
    """Full page. run_ts display string + data_last_bar drive the banner."""
    all_rows = sorted((r for rows in results.values() for r in rows),
                      key=lambda r: r["overall"], reverse=True)
    top10 = all_rows[:10]
    n = len(all_rows)
    regions = list(results.keys())
    stale = is_stale(_iso_from_display(run_ts), data_last_bar)
    last_bar_txt = data_last_bar if data_last_bar else "okänd"
    banner_cls = "sub stale" if stale else "sub"

    tabs = ['<div class="tabs" role="tablist">']
    tabs.append('<button class="tab-btn active" id="tab-all" role="tab" '
                'aria-selected="true" onclick="showRegion(\'all\',this)">'
                f'Alla ({n})</button>')
    for reg in regions:
        tabs.append('<button class="tab-btn" id="tab-' + reg + '" role="tab" '
                    'aria-selected="false" '
                    f'onclick="showRegion(\'{reg}\',this)">'
                    f'{REGION_LABELS.get(reg, reg)} ({len(results[reg])})</button>')
    tabs.append('<button class="tab-btn" id="tab-indicators" role="tab" '
                'aria-selected="false" onclick="showRegion(\'indicators\',this)">'
                'Indikatorer</button>')
    tabs.append('</div>')

    panels = [_table(all_rows, "all")]
    for reg in regions:
        panels.append(_table(results[reg], reg))
    panels.append(render_indikatorer_panel(indicators, indicators_note))

    top_rows = "".join(
        f'<tr><td>{r["ticker"]}</td>'
        f'<td class="{_signal_class(r["signal"])}">{r["signal"]}</td>'
        f'<td class="{_score_class(r["overall"])}">{r["overall"]:.1f}</td></tr>'
        for r in top10)

    universe_lines = "".join(
        f'<div class="region-note"><b>{REGION_LABELS.get(reg, reg)}</b> ({len(rows)}): '
        f'{", ".join(r["ticker"] for r in rows)}</div>'
        for reg, rows in results.items())

    stats_stamp = ""
    if indicators and indicators_note and indicators.get("generated_at"):
        stats_stamp = (f' · Indikatorstatistik från '
                       f'{indicators["generated_at"]} (bärs vidare)')

    return f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rocket — Stock Scanner Demo</title>{STYLE}</head>
<body><div class="wrap">
<header class="page"><h1>Rocket — Stock Scanner Demo</h1>
<div class="{banner_cls}" id="freshness" role="status">Data hämtad {run_ts} · Senaste kursdatum: {last_bar_txt}</div>
<div class="sub">Läs-only demo · {n} tickers i {len(regions)} regioner · scorerade med Rocket-score</div></header>
<h2>Rankings</h2>
<div style="margin-bottom:10px"><input type="text" placeholder="Filtrera ticker…" aria-label="Filtrera ticker" oninput="filterTable(this,'panel-all')"></div>
{''.join(tabs)}
{''.join(panels)}
<h2>Top 10 — köpsvy</h2>
<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Signal</th><th>Overall</th></tr></thead>
<tbody>{top_rows}</tbody></table></div>
<h2>Universe</h2>
{universe_lines}
<div class="footer">Genererad {run_ts} av scripts/generate_demo_page.py — data hämtas och sidan publiceras dagligen (systemd-timer rocket-demo-publish).{stats_stamp} · <a href="indicators.html">Metodik och indikatorförklaring</a></div>
</div>{JS}</body></html>"""


def _iso_from_display(run_ts: str) -> str:
    """'2026-10-02 05:00 UTC' -> '2026-10-02T05:00:00' for the stale rule."""
    try:
        dt = datetime.strptime(run_ts.replace(" UTC", ""), "%Y-%m-%d %H:%M")
    except ValueError:
        return run_ts
    return dt.strftime("%Y-%m-%dT%H:%M:%S")
