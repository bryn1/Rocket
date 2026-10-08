# reason: one subject — page rendering (DESIGN §3); past the 250 soft target,
# stays under the 400 hard ceiling.
"""HTML renderers for the daily demo page (MC 3874 → v3 MC 10223): pure
frame-data -> HTML. Same layout/CSS/structure/copy family (DESIGN §7): tabs
auto-generate from UniversePlan.order with REGION_META labels, Alla = Top-500
by score, the filter searches every panel (F3), and the Indikatorer tab
renders only the pinned indicator_stats.json fields (seam C3). No I/O, no
time/random reads: staleness and counts come from explicit args, so render is
deterministic for fixed inputs.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta

from full_universe import ALLA_CAP, region_label

# Guard markers (generate_demo_page._guard asserts these on the bytes).
BANNER_MARKER = 'id="freshness"'
TAB_ALL_MARKER = 'id="tab-all"'
INDICATOR_TAB_MARKER = 'id="tab-indicators"'
PANEL_PREFIX = 'id="panel-'
PANEL_ALL_MARKER = 'id="panel-all"'

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
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
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

# F3 fix (MC 3874 live bug, demo_render.py:62-67): the filter searched only
# the panel it was wired to, so with Alla capped at 500 most tickers were
# "invisible". ONE function, one status span, same input box: it searches
# every panel's ticker cells, reveals the hit and activates its region tab,
# surfacing "<TICKER> — <Region>" (or 'hittades inte').
JS = """<script>
function showRegion(id, btn){
  document.querySelectorAll('.region-panel').forEach(p=>p.style.display='none');
  document.getElementById('panel-'+id).style.display='block';
  document.querySelectorAll('.tab-btn').forEach(b=>{
    b.classList.remove('active'); b.setAttribute('aria-selected','false');});
  btn.classList.add('active');
  btn.setAttribute('aria-selected','true');
}
function filterTable(input){
  const q=input.value.trim().toUpperCase();
  const status=document.getElementById('filter-status');
  let first=null,hits=0;
  document.querySelectorAll('.region-panel').forEach(p=>{
    if(p.id==='panel-indicators')return;
    p.querySelectorAll('tbody tr').forEach(tr=>{
      const hit=!q||tr.cells[0].textContent.toUpperCase().includes(q);
      tr.style.display=hit?'':'none';
      if(hit&&q){hits++;if(p.id!=='panel-all'&&!first)
        first=[tr.cells[0].textContent,p.dataset.label||p.id.slice(6),p.id];}
    });
  });
  if(!status)return;
  if(!q){status.textContent='';return;}
  if(!hits){status.textContent='hittades inte';return;}
  if(!first){status.textContent=q+' (endast i Alla)';return;}
  status.textContent=first[0]+' — '+first[1];
  const tab=document.getElementById('tab-'+first[2].slice(6));
  if(tab)tab.click();
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
    """Freshness rule §5.5, weekday-aware (MC 3874 P2): markets are closed
    weekends, so stale means the newest bar predates the latest weekday
    (Mon-Fri) strictly before the fetch date — i.e. data is at least two
    trading days behind. The 05:00-UTC nightly seeing the previous calendar
    day's bars is FRESH; a weekend never counts as a missing trading day.
    THE rule (DESIGN §8/DA-c3 C3-F3b): the settled-bar gate reuses this one —
    no second freshness formula may exist. NOTE (C3-F3a): it has NO holiday
    calendar, so global-holiday nights can false-red on it alone; the gate's
    monotonic prev-green alternative covers that — never claim otherwise."""
    if not data_last_bar:
        return False                      # unknown bar: banner stays 'okänd'
    try:
        bar = datetime.strptime(data_last_bar[:10], "%Y-%m-%d")
        expected = datetime.strptime(run_iso[:10], "%Y-%m-%d")
    except ValueError:
        return False
    expected -= timedelta(days=1)
    while expected.weekday() >= 5:        # Sat/Sun fetch → step back to Friday
        expected -= timedelta(days=1)
    return bar < expected


def settled_gate_ok(data_last_bar: str, run_iso: str,
                    prev_green: str | None) -> bool:
    """§9 #6 settled-bar gate, C3-F3a correction: PASS when stored data has
    not regressed since the last green publish (monotonic freshness — covers
    holiday nights where the weekday rule, which has NO holiday calendar,
    false-reds), OR the weekday rule passes. Copy never claims holidays pass
    from the weekday rule alone. Moved out of the generator (MC 10264): the
    freshness rules keep ONE home — this gate composes is_stale above."""
    if prev_green and data_last_bar and data_last_bar >= prev_green:
        return True
    if not prev_green:
        print("settled-bar gate: ingen publicerad run_manifest — prövar "
              "veckodagsregeln ensam (saknar helgedagskalender)",
              file=sys.stderr)
    return not is_stale(run_iso, data_last_bar)


def _table(rows: list[dict], panel_id: str, label: str | None = None,
           note: str | None = None) -> str:
    data_attr = f' data-label="{label}"' if label else ""
    parts = [f'<div class="region-panel" id="panel-{panel_id}"{data_attr} '
             'role="tabpanel">']
    if note:
        parts.append(f'<div class="region-note">{note}</div>')
    parts.append('<div class="table-wrap"><table><thead><tr>'
                 '<th>Ticker</th><th>Signal</th><th>Overall</th><th>Momentum</th>'
                 '<th>Trend</th><th>Volatility</th><th>Volume</th><th>Close</th>'
                 '</tr></thead><tbody>')
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
    """Seam C3: renders ONLY pinned artifact fields; no doc -> honest text.
    The sample-size line reads engine.tickers of the document ACTUALLY
    rendered (F5): a carried 35-ticker artifact honestly says 35 under a
    12.8k header."""
    parts = ['<div class="region-panel" id="panel-indicators" role="tabpanel" '
             'style="display:none">',
             '<h2>Indikatorer — backtest</h2>']
    if note:
        parts.append(f'<div class="ind-note">{note}</div>')
    if not doc or not doc.get("indicators"):
        parts.append(f'<div class="ind-note">{EMPTY_BACKTEST_TEXT}</div></div>')
        return "\n".join(parts)
    eng = doc.get("engine") or {}
    if eng.get("tickers"):
        parts.append(
            f'<div class="region-note">Statistik på {eng["tickers"]} tickers '
            f'(veckovis urval, senast {doc.get("generated_at", "?")})</div>')
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


def render(plan, results: dict[str, list[dict]], run_ts: str,
           data_last_bar: str, indicators: dict | None = None,
           indicators_note: str | None = None) -> str:
    """Full page. plan = UniversePlan (order/labels/m_unique/registry_ts);
    results = {region: [row dicts]} scored tonight; run_ts display string +
    data_last_bar drive the banner."""
    all_rows = sorted((r for rows in results.values() for r in rows),
                      key=lambda r: r["overall"], reverse=True)
    top10 = all_rows[:10]
    n = len(all_rows)
    alla_rows = all_rows[:ALLA_CAP]
    regions = list(plan.order)
    stale = is_stale(run_ts, data_last_bar)
    last_bar_txt = data_last_bar if data_last_bar else "okänd"
    banner_cls = "sub stale" if stale else "sub"

    tabs = ['<div class="tabs" role="tablist">']
    tabs.append('<button class="tab-btn active" id="tab-all" role="tab" '
                'aria-selected="true" onclick="showRegion(\'all\',this)">'
                f'Alla ({len(alla_rows)})</button>')
    for reg in regions:
        tabs.append('<button class="tab-btn" id="tab-' + reg + '" role="tab" '
                    'aria-selected="false" '
                    f'onclick="showRegion(\'{reg}\',this)">'
                    f'{region_label(reg)} ({len(results.get(reg, []))})</button>')
    tabs.append('<button class="tab-btn" id="tab-indicators" role="tab" '
                'aria-selected="false" onclick="showRegion(\'indicators\',this)">'
                'Indikatorer</button>')
    tabs.append('</div>')

    panels = [_table(alla_rows, "all",
                     note=f"Alla · topp {ALLA_CAP} av {n}")]
    for reg in regions:
        panels.append(_table(results.get(reg, []), reg,
                             label=region_label(reg)))
    panels.append(render_indikatorer_panel(indicators, indicators_note))

    top_rows = "".join(
        f'<tr><td>{r["ticker"]}</td>'
        f'<td class="{_signal_class(r["signal"])}">{r["signal"]}</td>'
        f'<td class="{_score_class(r["overall"])}">{r["overall"]:.1f}</td></tr>'
        for r in top10)

    # Universe section (R4): per-region registry counts + the filter pointer
    # — never every ticker literally again (389 B today, ~115 KB at 12.8k).
    universe_lines = "".join(
        f'<div class="region-note"><b>{region_label(reg)}</b> '
        f'({len(plan.regions.get(reg, []))})</div>'
        for reg in regions)
    universe_lines += ('<div class="region-note">Hitta en ticker: filtret '
                       'ovan söker i alla regioner.</div>')

    stats_stamp = ""
    if indicators and indicators_note and indicators.get("generated_at"):
        stats_stamp = (f' · Indikatorstatistik från '
                       f'{indicators["generated_at"]} (bärs vidare)')
    dropped_note = (f' · {len(plan.dropped)} tickers utan primärregion'
                    if getattr(plan, "dropped", None) else "")

    return f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rocket — Stock Scanner Demo</title>{STYLE}</head>
<body><div class="wrap">
<header class="page"><h1>Rocket — Stock Scanner Demo</h1>
<div class="{banner_cls}" id="freshness" role="status">Data hämtad {run_ts} · Senaste kursdatum: {last_bar_txt}</div>
<div class="sub">Läs-only demo · scorerade {n} av {plan.m_unique} tickers i registret · {len(regions)} regioner · scorerade med Rocket-score</div></header>
<h2>Rankings</h2>
<div style="margin-bottom:10px"><input type="text" placeholder="Filtrera ticker…" aria-label="Filtrera ticker" oninput="filterTable(this)"> <span id="filter-status" class="region-note" role="status" style="display:inline;margin-left:8px"></span></div>
{''.join(tabs)}
{''.join(panels)}
<h2>Top 10 — köpsvy</h2>
<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Signal</th><th>Overall</th></tr></thead>
<tbody>{top_rows}</tbody></table></div>
<h2>Universe</h2>
{universe_lines}
<div class="footer">Genererad {run_ts} av scripts/generate_demo_page.py — data hämtas och sidan publiceras dagligen (systemd-timer rocket-demo-publish).{stats_stamp} · Registry {plan.registry_ts}{dropped_note} · <a href="indicators.html">Metodik och indikatorförklaring</a></div>
</div>{JS}</body></html>"""
