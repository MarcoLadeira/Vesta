"""The shipped HTML must say what brand.py says.

``brand.py`` opens by claiming to be "the one canonical source of product
identity and voice", and for the running app it is: the boot payload carries
the copy and the front end applies it. But ``index.html`` also holds a literal
copy of the same strings, and that copy is not decoration -- it is what a user
sees in the frame before the payload arrives.

So a copy change made only in ``brand.py`` leaves the app opening on the old
sentence and then swapping it, and a change made only in the HTML is
overwritten a moment later. Neither shows up in a test that renders the app,
because by the time anything is asserted the payload has landed.

This pins the pair that actually ships. The e2e mock holds a third copy, but
that one announces itself the moment a spec disagrees with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from opai import brand


_INDEX = Path(__file__).resolve().parents[1] / "opai" / "assets" / "web" / "index.html"


def _empty_state_html() -> str:
    source = _INDEX.read_text(encoding="utf-8")
    start = source.index('<div class="empty" id="empty">')
    return source[start : source.index("</div>", source.index('id="chips"'))]


def test_the_headline_matches():
    assert f"<h1>{brand.EMPTY_TITLE}</h1>" in _empty_state_html()


def test_the_body_matches():
    html = _empty_state_html()
    match = re.search(r'<p id="emptySub">(.*?)</p>', html, re.S)

    assert match is not None, "the empty state lost its body paragraph"
    assert match.group(1).strip() == brand.EMPTY_BODY


def test_the_markup_was_actually_found():
    """A slice that silently matched nothing would pass both tests above."""
    html = _empty_state_html()

    assert "<h1>" in html and 'id="emptySub"' in html


def test_the_empty_state_stays_short():
    """The change this file arrived with, kept.

    It was an eyebrow, a headline and a twenty-five word paragraph stacked
    above the actions. Nobody reads a mechanism before they have a reason to
    care, so what is left is a claim and its proof. This is a budget, not a
    style rule: if the body needs to grow past a line, that is a decision
    someone should have to make on purpose.
    """
    assert len(brand.EMPTY_BODY.split()) <= 10
    assert "empty-eyebrow" not in _INDEX.read_text(encoding="utf-8")
