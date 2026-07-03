"""Subprocess helpers that never flash a console window on Windows.

A GUI app (PySide6 / QtWebEngine) that shells out to a console program pops a
visible terminal for a split second on Windows. OPai collects git evidence
(status / diff), selects tests, and probes providers on *every* message, so
without suppression that becomes a burst of terminals flashing on screen each
time you send. ``CREATE_NO_WINDOW`` stops the child from ever getting a console.

``opaihub.accounts`` already applies this to the provider CLIs it launches;
this module is the shared source for every *other* subprocess OPai spawns, so
the behaviour is consistent and unit-testable in one place.
"""

from __future__ import annotations

import subprocess  # nosec B404 - this module only computes flags, never runs a shell
import sys
from typing import Any


def no_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` kwargs that stop a child console window flashing.

    On Windows this is ``{"creationflags": CREATE_NO_WINDOW}``; on every other
    platform there is no console-window concept, so it is an empty dict that
    leaves the call untouched.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
