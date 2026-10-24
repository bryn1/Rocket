"""Regenerate the Score column of region-top25.html from the same-epoch
index.html (MC 11188, owner ruling "1c" 2026-10-24).

The page is a frozen snapshot (ARCHITECTURE §8.3 #3): its Score column was
baked as ``0.00`` on all 346 rows — a dead column on a nav-linked live
route. ``top25.json`` is NOT its score source (zero ticker overlap, another
epoch), and ``source/data`` is OFF LIMITS (gitignored store, moves every
nightly). The committed source of truth for CURRENT live scores is the
published ``index.html`` itself: same epoch, every scored ticker on one row
carrying ticker / signal / score, one decimal displayed.

THE CONTRACT (one concern): rewrite ONLY the Score cells of
``region-top25.html`` to the displayed score; touch no rank, ticker, price
or date cell, no ordering. A page row whose ticker is absent from the
scored set gets a visible en-dash cell AND is listed, in page order, in one
small note under the tables — never a fake ``0.00`` (the exact failure this
card repairs; the note keeps absence honest and screen-reader visible
through the same text DOM as the table).

FAIL CLOSED, loudly, writing nothing, when the parsed source is not the
page we believe we are reading: fewer than ``MIN_SCORED_ROWS`` unique
scored tickers, fewer than ``MIN_REGIONS`` region panels, a score cell
that does not display as ``NN.N`` within ``[0.0, 100.0]``, the same ticker
scored twice with different values, zero Score cells matched in the
page, or a page whose header line and provenance p-meta are not shapes
this script can rewrite. A truncated or tampered index.html must not
reach the live page.

EPOCH HONESTY (MC 11188 DA round-1): the rewrite also owns the page's two
provenance claims — the ``<p class="sub">`` count line and the provenance
p-meta — because a page carrying Oct-24 scores must not claim a stale
count or the Sep-8 parquet as its Score source. The per-row ``Last date``
cells are NOT rewritten: that column is the PRICE datum (the prices in
this snapshot are the 2026-09-08 bars and stay so — the scored set is
scored, the prices are not refetched); rewriting them to the score epoch
would fake a quote the store evidence does not carry. Instead both epochs
are stated where they apply: Score = the index.html epoch, Price/Last
date = the snapshot date.

Re-running on the same index bytes is a byte-exact no-op: the note, the
header line and the p-meta are regenerated from the parsed truth, never
nested.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MIN_SCORED_ROWS = 9000
MIN_REGIONS = 15
ENDASH = "\u2013"
SCORE_NOTE_PREFIX = "Ej scoreade i dagens körr: "
NOTE_OPEN = "<!-- missing-note:start -->"
NOTE_CLOSE = "<!-- missing-note:end -->"

# index.html panels: `<div class="region-panel" id="panel-<key>" role=...>`.
# The all-panel is a top-500 subset and the indicators panel is not scored
# rows — both are excluded from the scored set; every other panel is a
# region.
PANEL_RE = re.compile(r'<div class="region-panel" id="panel-([a-z0-9._-]+)"'
                      r'[^>]*>')
NOT_A_REGION_PANEL = frozenset({"all", "indicators"})
# One scored row as published by generate_demo_page.py / demo_render.py.
ROW_RE = re.compile(
    r'<tr><td>([^<]+)</td>'
    r'<td class="(buy|sell|hold)">[^<]*</td>'
    r'<td class="score-[a-z]+">([^<]*)</td>')
# Displayed score on the published page: one decimal, 0.0–100.0.
SCORE_SHAPE_RE = re.compile(r"\d{1,3}\.\d")
# region-top25.html row: the Score cell is the td.num DIRECTLY after the
# ticker cell; the Price cell (second td.num) can never be matched.
SCORE_CELL_RE = re.compile(
    r'(<td class="ticker">([^<]+)</td><td class="num">)([^<]*)(</td>)')
PROVENANCE_MARKER = '<div class="provenance">'

# Epoch honesty (DA round-1): the claims rewritten below. The SHA pins the
# publish commit of the index.html whose scores are copied; the date is
# that publish. The PRICE snapshot date is the frozen Last-date datum —
# stated, never rewritten into the score epoch.
SCORE_EPOCH_SHA = "031b975"
SCORE_EPOCH_DATE = "2026-10-24"
PRICE_SNAPSHOT_DATE = "2026-09-08"
# The header count line: the old English claim OR this script's own Swedish
# replacement (idempotent re-match). Note paragraphs start with a letter
# ("Ej scoreade…") and can never collide with the leading digit.
HEADER_SUB_RE = re.compile(r'<p class="sub">\d+ (?:scored|scoreade)[^<]*</p>')
# The provenance p-meta block, rewritten whole from parsed truth.
PROV_META_RE = re.compile(r'<div class="p-meta">.*?</div>', re.DOTALL)


class RegenError(RuntimeError):
    """Fail-closed refusal: the source is not what we are asked to copy."""


def parse_index(html: str) -> tuple[dict[str, str], int]:
    """(ticker -> displayed score, region-panel count) from an index.html.

    Guards raise RegenError naming the exact shortfall; nothing downstream
    runs when they fire."""
    parts = PANEL_RE.split(html)
    names, bodies = parts[1::2], parts[2::2]
    regions = [n for n in names if n not in NOT_A_REGION_PANEL]
    if len(regions) < MIN_REGIONS:
        raise RegenError(
            f"index.html: {len(regions)} region panels, floor is "
            f"{MIN_REGIONS} — refusing")
    scores: dict[str, str] = {}
    for name, body in zip(names, bodies):
        if name in NOT_A_REGION_PANEL:
            continue
        for ticker, _signal, score in ROW_RE.findall(body):
            if not SCORE_SHAPE_RE.fullmatch(score):
                raise RegenError(
                    f"index.html: panel {name!r} ticker {ticker!r} score "
                    f"{score!r} is not a displayed score (NN.N) — refusing")
            if not 0.0 <= float(score) <= 100.0:
                raise RegenError(
                    f"index.html: ticker {ticker!r} score {score!r} outside "
                    "0.0–100.0 — refusing")
            if scores.setdefault(ticker, score) != score:
                raise RegenError(
                    f"index.html: ticker {ticker!r} scored twice with "
                    f"different values ({scores[ticker]!r} vs {score!r})")
    if len(scores) < MIN_SCORED_ROWS:
        raise RegenError(
            f"index.html: only {len(scores)} scored tickers, floor is "
            f"{MIN_SCORED_ROWS} — refusing")
    return scores, len(regions)


def rewrite_page(page_html: str, scores: dict[str, str], n_regions: int
                 ) -> tuple[str, int, list[str]]:
    """Rewrite the Score cells, the header count line and the provenance
    p-meta; return (html, real-score count, missing).

    ``missing`` lists, in page order, the tickers absent from the scored
    set: their cells become a visible en-dash and the list is rendered into
    one small note under the tables. Per-row Price/Last-date cells are
    deliberately NOT touched — that is the frozen price datum (see the
    module docstring)."""
    # Drop a previously generated note first: re-runs regenerate it, never
    # stack it, and a fully-scored page sheds the stale list.
    if NOTE_OPEN in page_html:
        head, _, tail = page_html.partition(NOTE_OPEN)
        _old, _sep, rest = tail.partition(NOTE_CLOSE)
        if not _sep:
            raise RegenError("page: missing-note block is not closed — refusing")
        page_html = head + rest.lstrip("\n")

    missing: list[str] = []

    def _cell(match: re.Match) -> str:
        head, ticker, _old, tail = match.groups()
        score = scores.get(ticker)
        if score is None:
            missing.append(ticker)
            return head + ENDASH + tail
        return head + score + tail

    new_html, n_cells = SCORE_CELL_RE.subn(_cell, page_html)
    if n_cells == 0:
        raise RegenError(
            "region-top25.html: no Score cell matched — refusing to call "
            "that a rewrite")
    stale = len(re.findall(
        r'<td class="ticker">[^<]+</td><td class="num">0\.00</td>', new_html))
    if stale:
        raise RegenError(
            f"region-top25.html: {stale} Score cells still show the baked "
            "0.00 after the rewrite — refusing")

    # Epoch honesty: the header count line and the provenance p-meta are
    # regenerated from the parsed truth. Shapes unknown => refuse.
    n_rows = n_cells
    header = (f'<p class="sub">{len(scores)} scoreade tickers '
              f'({SCORE_EPOCH_DATE}) across {n_regions} regions · Score: '
              f'index.html {SCORE_EPOCH_SHA} · Price/Last date: snapshot '
              f'{PRICE_SNAPSHOT_DATE}</p>')
    new_html, n_head = HEADER_SUB_RE.subn(lambda _m: header, new_html, count=1)
    if n_head == 0:
        raise RegenError(
            "region-top25.html: header count line not matched — refusing")
    p_meta = (
        '<div class="p-meta">\n'
        '<span class="p-item">Score source <span class="p-val">index.html '
        f'{SCORE_EPOCH_SHA} ({SCORE_EPOCH_DATE}, regenererad av '
        'scripts/regen_top25_scores.py, MC 11188)</span></span>\n'
        '<span class="p-item">Price/Last date <span class="p-val">'
        f'{PRICE_SNAPSHOT_DATE} snapshot — fryst, ej regenererat</span>'
        '</span>\n'
        f'<span class="p-item">Scored tickers <span class="p-val">{len(scores)}'
        f'</span></span>\n'
        f'<span class="p-item">Regions <span class="p-val">{n_regions}</span>'
        '</span>\n'
        f'<span class="p-item">Rows <span class="p-val">{n_rows}</span></span>'
        '\n<span class="p-item">Order/labels <span class="p-val">C4 '
        'universe_regions</span></span>\n</div>')
    new_html, n_meta = PROV_META_RE.subn(lambda _m: p_meta, new_html, count=1)
    if n_meta == 0:
        raise RegenError(
            "region-top25.html: provenance p-meta not matched — refusing")

    if missing:
        if PROVENANCE_MARKER not in new_html:
            raise RegenError(
                "region-top25.html: provenance block not found — nowhere to "
                "attach the ej-scoreade note — refusing")
        note = (f'{NOTE_OPEN}<p class="sub">{SCORE_NOTE_PREFIX}'
                f'{", ".join(missing)}</p>{NOTE_CLOSE}')
        new_html = new_html.replace(
            PROVENANCE_MARKER, note + "\n" + PROVENANCE_MARKER, 1)
    return new_html, n_cells - len(missing), missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", type=Path, default=REPO_ROOT / "index.html",
                    help="published page to read scores from")
    ap.add_argument("--page", type=Path,
                    default=REPO_ROOT / "region-top25.html",
                    help="page whose Score column is regenerated (in place)")
    args = ap.parse_args(argv)
    try:
        scores, n_regions = parse_index(
            args.index.read_text(encoding="utf-8"))
        page_html = args.page.read_text(encoding="utf-8")
        new_html, n_real, missing = rewrite_page(page_html, scores, n_regions)
    except RegenError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 1
    args.page.write_text(new_html, encoding="utf-8")
    print(f"scored-set={len(scores)} · regions={n_regions} · epoch="
          f"index.html@{SCORE_EPOCH_SHA} ({SCORE_EPOCH_DATE}) · "
          f"cells-rewritten={n_real + len(missing)} · real={n_real} · "
          f"en-dash={len(missing)}")
    if missing:
        print(SCORE_NOTE_PREFIX + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
