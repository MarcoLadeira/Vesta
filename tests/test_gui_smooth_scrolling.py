"""Smooth scrolling has to be asked for, and asked for early.

Chromium enables it by default; QtWebEngine does not, so a wheel notch jumps
the thread by a fixed number of pixels instead of animating to the new
position. That is the difference between a thread that feels like it is
running at 40Hz and one that feels like the display's refresh rate.

The flag is read when QtWebEngine initialises, so setting it after an
application object exists does nothing at all -- silently. These pin the two
things that make it work: it is appended rather than assigned, and it is set
before anything imports the web engine.
"""

from __future__ import annotations

import inspect
import os

from opai import gui_web


def test_the_flag_is_set_when_nothing_was_there(monkeypatch):
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)

    gui_web._enable_smooth_scrolling()

    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] == "--enable-smooth-scrolling"


def test_flags_the_user_already_set_survive(monkeypatch):
    """Assigning would silently drop someone's own Chromium configuration."""
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --lang=en")

    gui_web._enable_smooth_scrolling()

    value = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert "--disable-gpu" in value and "--lang=en" in value
    assert "--enable-smooth-scrolling" in value


def test_it_is_not_added_twice(monkeypatch):
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-smooth-scrolling")

    gui_web._enable_smooth_scrolling()

    assert os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].count("smooth-scrolling") == 1


def test_it_runs_before_the_web_engine_is_imported():
    """The ordering is the whole feature.

    QtWebEngine reads the flags as it initialises. Called after the PySide6
    imports, this function would still set the variable, the test above would
    still pass, and scrolling would be exactly as it was.
    """
    source = inspect.getsource(gui_web._run_gui)
    call = source.index("_enable_smooth_scrolling()")
    first_qt_import = source.index("from PySide6")

    assert call < first_qt_import
