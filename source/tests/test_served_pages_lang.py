"""Served-page language guard (MC 11188, d7 F2).

The four committed root pages are Swedish content; region-top25.html shipped
`lang="en"` while painting Swedish region labels (WCAG 3.1.1 — screen readers
pronounce Swedish with English rules). This guards the CLASS, not the one
page: every committed served page must declare lang="sv".
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["index.html", "indicators.html", "portfolio.html",
         "region-top25.html"]


@pytest.mark.parametrize("page", PAGES)
def test_served_page_declares_swedish(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    m = re.search(r'<html[^>]*\blang="([^"]+)"', html)
    assert m, f'{page}: no lang attribute on <html>'
    assert m.group(1) == "sv", (
        f'{page}: lang="{m.group(1)}" but content is Swedish '
        f'(WCAG 3.1.1); expected lang="sv"')
