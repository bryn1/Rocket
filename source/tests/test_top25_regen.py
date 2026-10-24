"""Hermetic tests for the Score-column regen (MC 11188, owner "1c").

Fixtures are SYNTHETIC index.html / region-top25.html strings written under
tmp_path — the tests never read source/data and never touch the real
committed pages. Every guard carries its own planted-bad row (planted-bad
RED pairs per FIX-WAVE rule); the absent-ticker test is the behaviour that
did not exist before this card.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import regen_top25_scores as rts  # noqa: E402

# ---------------------------------------------------------------- fixtures

PAGE_TMPL = """<!doctype html>
<html lang="sv"><body>
<header class="page"><h1>Rocket — Top 25 per region</h1>
<p class="sub">10300 scored tickers across 15 regions · as of latest M6 store</p>
</header>
<section class="region" id="region-test">
<table class="board">
<tbody>
ROWS
</tbody>
</table>
</section>
<div class="provenance"><span class="p-badge">authentic · per-region top-25</span>
<div class="p-meta">
<span class="p-item">Source <span class="p-val">/srv/workspace/svarkor-rocket-25k-data-collect/store/scores/scores_2026-09-08.parquet</span></span>
<span class="p-item">Rows <span class="p-val">346</span></span>
</div>
</div>
</body></html>
"""


def scored_row(ticker: str, score: str, signal: str = "BUY") -> str:
    sig_cls = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}[signal]
    return (f'<tr><td>{ticker}</td><td class="{sig_cls}">{signal}</td>'
            f'<td class="score-hi">{score}</td><td>60.0</td><td>65.0</td>'
            f'<td>50.0</td><td>55.0</td><td>10.5</td></tr>')


def score_of(n: int) -> str:
    """Deterministic one-decimal score, inside 0.0–100.0."""
    return f"{30.0 + (n % 45) + (n % 10) / 10:.1f}"


def build_index(region_counts: dict[str, int],
                tamper: tuple[str, int, str] | None = None,
                extra_dup: str | None = None) -> str:
    """Synthetic published page. ``tamper`` replaces the score cell of
    ``(region, row-index)`` with raw text; ``extra_dup`` injects a second
    row for that ticker carrying a conflicting score."""
    out = ['<!doctype html><html lang="sv"><body>']
    n = 0
    seen: list[str] = []
    for region, count in region_counts.items():
        out.append(f'<div class="region-panel" id="panel-{region}" '
                   'role="tabpanel"><table><tbody>')
        for i in range(count):
            n += 1
            ticker = f"T{n:06d}"
            seen.append(ticker)
            score = score_of(n)
            if tamper and (region, i) == tamper[:2]:
                score = tamper[2]
            out.append(scored_row(ticker, score))
        out.append("</tbody></table></div>")
    if extra_dup is not None:
        out.append('<div class="region-panel" id="panel-dup" role="tabpanel">'
                   f'<table><tbody>{scored_row(extra_dup, "99.9")}'
                   "</tbody></table></div>")
    out.append("</body></html>")
    return "\n".join(out)


def full_regions(count: int = 601) -> dict[str, int]:
    """15 regions x count rows; 15 x 601 = 9015 > the 9000 floor."""
    return {f"region{i}": count for i in range(15)}


def build_top25(tickers: list[tuple[str, str]]) -> str:
    """region-top25-shaped page: (rank, ticker) rows with the OLD baked
    0.00 Score cell, plus price and date columns."""
    rows = "\n".join(
        f'<tr><td class="rank">{rank}</td><td class="ticker">{ticker}</td>'
        '<td class="num">0.00</td><td class="num">12.34</td>'
        '<td>2026-09-08</td></tr>'
        for rank, ticker in tickers)
    return PAGE_TMPL.replace("ROWS", rows)


def cell_of(html: str, ticker: str) -> str:
    import re
    m = re.search(rf'<td class="ticker">{re.escape(ticker)}</td>'
                  r'<td class="num">([^<]*)</td>', html)
    assert m, f"no Score cell for {ticker}"
    return m.group(1)


# ---------------------------------------------------------------- parse guards

def test_parses_full_scored_set():
    scores, regions = rts.parse_index(build_index(full_regions()))
    assert len(scores) == 15 * 601 == 9015
    assert regions == 15
    assert scores["T000001"] == score_of(1)
    assert all(rts.SCORE_SHAPE_RE.fullmatch(s) for s in scores.values())


def test_below_scored_floor_refuses():
    # planted-bad: 14 x 601 + 1 x 394 = 8999 unique — one row short.
    counts = {f"region{i}": 601 for i in range(14)}
    counts["region14"] = 394
    with pytest.raises(rts.RegenError, match="floor is 9000"):
        rts.parse_index(build_index(counts))


def test_below_region_floor_refuses():
    counts = {f"region{i}": 700 for i in range(14)}  # 14 panels, rows fine
    with pytest.raises(rts.RegenError, match="floor is 15"):
        rts.parse_index(build_index(counts))


def test_tampered_score_shape_refuses():
    html = build_index(full_regions(), tamper=("region3", 7, "1234"))
    with pytest.raises(rts.RegenError, match="not a displayed score"):
        rts.parse_index(html)


def test_tampered_score_out_of_range_refuses():
    html = build_index(full_regions(), tamper=("region3", 7, "120.0"))
    with pytest.raises(rts.RegenError, match="outside"):
        rts.parse_index(html)


def test_conflicting_duplicate_refuses():
    dup = "T000123"
    html = build_index(full_regions(), extra_dup=dup)
    with pytest.raises(rts.RegenError, match="scored twice"):
        rts.parse_index(html)


# ---------------------------------------------------------------- rewrite

def test_rewritten_cells_equal_fixture_values():
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001"), ("2", "T000400"),
                        ("3", "T009015"), ("4", "T000002")])
    new_html, n_real, missing = rts.rewrite_page(page, scores, _regions)
    assert missing == [] and n_real == 4
    for ticker in ("T000001", "T000400", "T009015", "T000002"):
        assert cell_of(new_html, ticker) == scores[ticker]


def test_absent_ticker_gets_endash_and_note():
    # RED before this card: the cell kept its baked 0.00 and no note existed.
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "GONE.OL"), ("2", "T000001"),
                        ("3", "ALSO-GONE.ST")])
    new_html, n_real, missing = rts.rewrite_page(page, scores, _regions)
    assert missing == ["GONE.OL", "ALSO-GONE.ST"]
    assert n_real == 1
    assert cell_of(new_html, "GONE.OL") == rts.ENDASH
    assert cell_of(new_html, "ALSO-GONE.ST") == rts.ENDASH
    assert new_html.count(rts.SCORE_NOTE_PREFIX) == 1
    assert ("Ej scoreade i dagens körr: GONE.OL, ALSO-GONE.ST" in new_html
            )
    note_pos = new_html.index(rts.SCORE_NOTE_PREFIX)
    assert note_pos > new_html.index("</table>"), "note must sit under the table"
    assert note_pos < new_html.index(rts.PROVENANCE_MARKER)


def test_no_fake_zero_cell_survives():
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001"), ("2", "GONE.OL")])
    new_html, _n, _m = rts.rewrite_page(page, scores, _regions)
    assert '<td class="num">0.00</td>' not in new_html


def test_zero_cells_matched_refuses():
    scores, _regions = rts.parse_index(build_index(full_regions()))
    empty = PAGE_TMPL.replace("ROWS", "")
    with pytest.raises(rts.RegenError, match="no Score cell matched"):
        rts.rewrite_page(empty, scores, _regions)


def test_epoch_claims_rewritten():
    # planted RED: if the rewrite ever lets the stale epoch claims survive,
    # these assertions go red — the page must not claim 10300 or the
    # Sep-8 parquet once it carries the 031b975 scores.
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001"), ("2", "GONE.OL")])
    new_html, _n, _m = rts.rewrite_page(page, scores, _regions)
    assert "scores_2026-09-08" not in new_html
    assert "10300" not in new_html
    assert "as of latest M6 store" not in new_html
    assert f"9015 scoreade tickers ({rts.SCORE_EPOCH_DATE})" in new_html
    assert rts.SCORE_EPOCH_SHA in new_html
    assert "GONE.OL" in new_html.split(rts.SCORE_NOTE_PREFIX)[1]


def test_price_dates_stay_in_price_epoch():
    # The Last-date column is the PRICE datum: it stays 2026-09-08 (true
    # for the snapshot prices) and is never faked into the score epoch.
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001"), ("2", "GONE.OL")])
    new_html, _n, _m = rts.rewrite_page(page, scores, _regions)
    assert new_html.count(f"<td>{rts.PRICE_SNAPSHOT_DATE}</td>") == 2
    assert f"<td>{rts.SCORE_EPOCH_DATE}</td>" not in new_html
    assert f"{rts.PRICE_SNAPSHOT_DATE} snapshot" in new_html


@pytest.mark.parametrize("remove", ["header", "p-meta"])
def test_unknown_epoch_shapes_refuse(remove):
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001")])
    if remove == "header":
        page = page.replace('<p class="sub">10300 scored tickers across 15 '
                            "regions · as of latest M6 store</p>", "")
        with pytest.raises(rts.RegenError, match="header count line"):
            rts.rewrite_page(page, scores, _regions)
    else:
        page = re.sub(r'<div class="p-meta">.*?</div>', "", page,
                      flags=re.DOTALL)
        with pytest.raises(rts.RegenError, match="p-meta"):
            rts.rewrite_page(page, scores, _regions)


def test_price_cell_never_touched():
    scores, _regions = rts.parse_index(build_index(full_regions()))
    page = build_top25([("1", "T000001"), ("2", "GONE.OL")])
    new_html, _n, _m = rts.rewrite_page(page, scores, _regions)
    import re
    prices = re.findall(r'</td><td class="num">([^<]*)</td><td>2026',
                        new_html)
    assert prices == ["12.34", "12.34"]


# ---------------------------------------------------------------- main() seam

def write_pair(tmp_path: Path, page: str) -> tuple[Path, Path]:
    index = tmp_path / "index.html"
    target = tmp_path / "region-top25.html"
    index.write_text(build_index(full_regions()), encoding="utf-8")
    target.write_text(page, encoding="utf-8")
    return index, target


def test_main_guard_fail_writes_nothing(tmp_path):
    index = tmp_path / "index.html"
    target = tmp_path / "region-top25.html"
    counts = {f"region{i}": 601 for i in range(14)}
    counts["region14"] = 394  # below the scored floor: planted-bad source
    index.write_text(build_index(counts), encoding="utf-8")
    before = build_top25([("1", "T000001")])
    target.write_text(before, encoding="utf-8")
    assert rts.main(["--index", str(index), "--page", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == before


def test_main_writes_and_is_idempotent(tmp_path):
    index, target = write_pair(
        tmp_path, build_top25([("1", "T000001"), ("2", "GONE.OL")]))
    assert rts.main(["--index", str(index), "--page", str(target)]) == 0
    once = target.read_text(encoding="utf-8")
    assert cell_of(once, "T000001") == score_of(1)
    assert cell_of(once, "GONE.OL") == rts.ENDASH
    assert rts.main(["--index", str(index), "--page", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == once, "re-run must be a no-op"
    assert once.count(rts.SCORE_NOTE_PREFIX) == 1
