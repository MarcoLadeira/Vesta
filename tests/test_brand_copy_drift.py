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

from pathlib import Path

from opai import brand


_INDEX = Path(__file__).resolve().parents[1] / "opai" / "assets" / "web" / "index.html"


def _empty_state_html() -> str:
    source = _INDEX.read_text(encoding="utf-8")
    start = source.index('<div class="empty" id="empty">')
    return source[start : source.index("</div>", source.index('id="chips"'))]


def test_the_headline_matches():
    assert f"<h1>{brand.EMPTY_TITLE}</h1>" in _empty_state_html()


def test_the_markup_was_actually_found():
    """A slice that silently matched nothing would pass the test above."""
    html = _empty_state_html()

    assert "<h1>" in html and 'id="emptySub"' in html


def test_nothing_on_the_empty_state_only_describes():
    """What is left is a mark, a claim, and three things you can click.

    The body and the keyboard hint went because neither changed what the
    reader does next: one described the product to someone already looking at
    it, the other taught a shortcut for a thing they had not done yet. The
    paragraph stays in the markup but starts empty and hidden, because it has
    one real job left -- saying that no provider is connected, which is the
    one sentence here that is worth a line.
    """
    html = _empty_state_html()

    assert '<p id="emptySub" hidden></p>' in html
    assert "Ctrl" not in html
    assert not hasattr(brand, "EMPTY_BODY")
    assert not hasattr(brand, "EMPTY_HINT")
