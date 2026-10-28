"""B3 (MC 11474, card MC 11560): the v2 page marker is GUARD-PINNED (PLAN-c3
§4). The constant lives in demo_render, generate_demo_page._markers() demands
it, and its absence on the rendered bytes fails the §5 fail-closed guard —
the wired product claim ("Rocket-score v2 — konfidens, risk och regime vägs
in") can never silently vanish from a published page. The file also pins the
D2 KNOWN LIMIT line renders on the page (DECISIONS.md: no sentiment service
on this host → social_score stays 0.0 until one ships — stated, not hidden).
Shared dtgen/fixture loaders reused (test_guard_ceil_floors idiom); no
source/data, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import demo_render                                   # noqa: E402
import test_demo_page_generator as dtgen             # noqa: E402 (shared gen load)
from demo_page_fixtures import FRESH_BAR, PLAN       # noqa: E402

gen = dtgen.gen
floors_patched = dtgen.floors_patched   # §9 floors via constants (shared idiom)


def test_v2_marker_constant_pinned():
    assert demo_render.SCORE_V2_MARKER == 'id="score-v2"'
    assert "Rocket-score v2" in demo_render.SCORE_V2_TEXT
    assert "konfidens, risk och regime" in demo_render.SCORE_V2_TEXT


def test_v2_marker_is_a_guard_marker_with_updated_count():
    ms = gen._markers(PLAN)
    assert demo_render.SCORE_V2_MARKER in ms
    assert len(ms) == 5 + len(PLAN.order)   # base 4 + v2, never a fixed list


def test_page_renders_v2_marker_and_known_limit_line():
    html = dtgen._html()
    assert demo_render.SCORE_V2_MARKER in html
    assert demo_render.SCORE_V2_TEXT in html
    # D2 KNOWN LIMIT stated on the page (gate ships, not the data):
    assert "topp-500" in html and "2 s" in html
    assert "social_score 0.0" in html
    assert "tills en t" in html             # "…0.0 … until one ships"


def test_guard_green_with_marker(floors_patched):
    assert dtgen._guard(prev=FRESH_BAR) == []


def test_guard_red_when_marker_removed(floors_patched):
    crippled = dtgen._html().replace('id="score-v2"', 'id="x"')
    reasons = dtgen._guard(html=crippled, prev=FRESH_BAR)
    assert 'marker saknas/ogiltig: id="score-v2"' in reasons
    assert sum(1 for r in reasons if "marker saknas" in r) == 1
