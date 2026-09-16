"""App-wide colour theme for the desktop GUI.

The choices are Light, Viber Coder (Vesta's original night sky, and the
default), Dark (midnight: black and grey, no colour), Vesta (the Vesta logo's
cream, dusty rose and sky blue), and System, which follows the OS between Light
and Viber Coder.

Appearance preferences such as density live with each project
(``vestahub.gui_preferences``). The theme does not. It is how the application
looks, not how a repository is worked on, so switching folders must never
repaint the window, and a theme chosen once must hold in every workspace and
across every update. It is kept beside the other app-wide GUI state in
``~/.vesta``.

Qt-free, so the persistence and the first-paint stamp are testable headlessly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vesta.legacy import home_item
from vestahub.atomic_io import atomic_write_text

# Preferences a user can choose, and the palettes they paint with. Every
# preference but "system" is a palette of the same name.
THEMES = ("light", "viber-coder", "dark", "vesta", "system")
PALETTES = ("light", "viber-coder", "dark", "vesta")
DEFAULT_THEME = "viber-coder"
# What "system" becomes when the OS is dark: the brand's own night sky, not the
# blackout theme.
SYSTEM_DARK_THEME = "viber-coder"

# The window's own background, painted before the page has drawn anything.
# These are the ``--bg`` of each palette in assets/web/design-tokens.css -- a
# test holds them equal -- so a launch never flashes another theme's ground.
THEME_GROUND = {
    "light": "#eef1f6",
    "viber-coder": "#04050f",
    "dark": "#000000",
    "vesta": "#f6efe7",
}


def theme_path() -> Path:
    return home_item(Path.home(), "gui_theme.json")


def normalize_theme(value: object) -> str:
    """A known theme preference, or the default for anything else."""
    text = str(value if value is not None else "").strip().lower()
    return text if text in THEMES else DEFAULT_THEME


def load_theme() -> str:
    """The saved preference. A missing, unreadable or corrupt file is the default."""
    try:
        data = json.loads(theme_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_THEME
    if not isinstance(data, dict):
        return DEFAULT_THEME
    return normalize_theme(data.get("theme"))


def save_theme(value: object) -> str:
    """Persist a preference and return what was stored.

    An unknown value is stored as the default rather than refused: the page
    has already repainted, and the next launch should agree with it about as
    much as it safely can.
    """
    theme = normalize_theme(value)
    atomic_write_text(theme_path(), json.dumps({"theme": theme}) + "\n")
    return theme


def resolve_theme(preference: object, *, system_prefers_light: bool = False) -> str:
    """Turn a preference into the palette to paint (one of ``PALETTES``)."""
    theme = normalize_theme(preference)
    if theme == "system":
        return "light" if system_prefers_light else SYSTEM_DARK_THEME
    return theme


_HTML_START_TAG = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_THEME_ATTRIBUTE = re.compile(
    r"\sdata-theme\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE
)


def stamp_theme(html: str, theme: str) -> str:
    """Put the resolved theme on ``<html>`` so the first paint already wears it.

    The page's own script applies the theme too, but only once the bridge has
    booted; without the stamp a light-theme user would watch the window start
    in the default theme and then change.
    """
    resolved = theme if theme in PALETTES else DEFAULT_THEME
    match = _HTML_START_TAG.search(html)
    if match is None:
        return html
    tag = _THEME_ATTRIBUTE.sub("", match.group(0))
    tag = f'{tag[:-1].rstrip()} data-theme="{resolved}">'
    return html[: match.start()] + tag + html[match.end() :]
