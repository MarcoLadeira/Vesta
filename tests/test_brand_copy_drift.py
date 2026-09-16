"""The shipped HTML must say what brand.py says.

``brand.py`` opens by claiming to be "the one canonical source of product
identity and voice", and for the running app it is: the boot payload carries
the copy and the front end applies it. But ``index.html`` is also a copy of the
screen, and that copy is not decoration -- it is what a user sees in the frame
before the payload arrives.

The headline is the sharpest case. It is a motto drawn once per launch, so no
literal in the HTML can be right: whatever it said, most launches would open on
it and then swap it. So the shipped headline is blank, the launch copy is
stamped with this run's motto, and the front end only fills a headline that
arrived empty. None of that shows up in a test that renders the app, because by
the time anything is asserted the payload has landed.

This pins the pieces that actually ship, and the two test copies that would
otherwise drift away from the collection without anyone noticing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vesta import brand
from vesta.gui_web import _stamp_empty_title


_WEB = Path(__file__).resolve().parents[1] / "vesta" / "assets" / "web"
_INDEX = _WEB / "index.html"
_E2E = _WEB / "__tests__" / "e2e"


def _empty_state_html(source: str | None = None) -> str:
    source = _INDEX.read_text(encoding="utf-8") if source is None else source
    start = source.index('<div class="empty" id="empty">')
    return source[start : source.index("</div>", source.index('id="chips"'))]


def test_the_shipped_headline_is_blank():
    """Any sentence here would be on screen before the launch's own motto."""
    assert "<h1></h1>" in _empty_state_html()
    assert "Better. Faster. Cheaper." not in _INDEX.read_text(encoding="utf-8")


def test_the_launch_copy_opens_on_this_runs_motto():
    stamped = _stamp_empty_title(_INDEX.read_text(encoding="utf-8"))

    assert f"<h1>{brand.empty_title()}</h1>" in _empty_state_html(stamped)
    # Stamped once per launch, and a second window in the same run agrees.
    assert _stamp_empty_title(_INDEX.read_text(encoding="utf-8")) == stamped


def test_the_markup_was_actually_found():
    """A slice that silently matched nothing would pass the tests above."""
    html = _empty_state_html()

    assert "<h1>" in html and 'id="emptySub"' in html


def test_nothing_on_the_empty_state_only_describes():
    """What is left is a mark, a motto, and three things you can click.

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


def test_the_e2e_mock_speaks_an_approved_motto():
    source = (_E2E / "mock-bridge.js").read_text(encoding="utf-8")
    match = re.search(r'emptyTitle: ("[^"]*")', source)

    assert match, "mock-bridge.js no longer sets brand.emptyTitle"
    assert json.loads(match.group(1)) in brand.VESTA_MOTTOS


def test_the_layout_spec_covers_every_motto():
    """session-motto.spec.js lays out each motto; a new one must be in it."""
    source = (_E2E / "session-motto.spec.js").read_text(encoding="utf-8")
    block = re.search(r"const VESTA_MOTTOS = \[(.*?)\];", source, re.S)

    assert block, "session-motto.spec.js no longer declares VESTA_MOTTOS"
    mottos = tuple(json.loads(m) for m in re.findall(r'"[^"]*"', block.group(1)))
    assert mottos == brand.VESTA_MOTTOS
