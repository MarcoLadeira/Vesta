"""Desktop window identity: packaged icon + Windows taskbar grouping (#148).

Shared by both GUI hosts (``gui_web`` and the classic ``gui_desktop``) so the
window, taskbar, and Alt-Tab all show the OPai icon instead of the generic
Python one. The Qt-facing work is kept behind a tiny, injectable surface so the
logic is unit-testable without a running Qt application or a display.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from opai.brand import NAME, app_icon_path
from opai.release_identity import current_release_identity

# A stable AppUserModelID lets Windows group the app under its own window icon
# rather than the host launcher's (pythonw.exe). Must be set before the first
# window is shown to take effect.
WINDOWS_APP_ID = "OPai.Desktop"


def set_windows_app_id(app_id: str = WINDOWS_APP_ID) -> bool:
    """Set the Windows AppUserModelID so the taskbar uses our window icon.

    No-op and ``False`` off Windows or if the shell call is unavailable; never
    raises. Returns ``True`` only when the identity was actually applied.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes  # noqa: PLC0415 - Windows-only, imported lazily

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:  # noqa: BLE001 - a cosmetic taskbar hint must never crash launch
        return False


def apply_window_identity(
    app: Any,
    window: Any,
    *,
    icon_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Apply the packaged icon and product identity to a Qt app + window.

    ``icon_factory`` builds a ``QIcon`` from a path; it defaults to the real
    ``PySide6.QtGui.QIcon`` but is injectable so tests need no Qt/display. Every
    step is best-effort — a missing icon or an old Qt binding degrades to the
    default window chrome rather than blocking the GUI from opening.
    """
    result = {
        "icon_set": False,
        "app_name_set": False,
        "app_version_set": False,
        "windows_app_id_set": False,
    }

    path = app_icon_path()
    if path is not None:
        if icon_factory is None:
            try:
                from PySide6 import QtGui  # noqa: PLC0415 - lazy Qt import

                icon_factory = QtGui.QIcon
            except Exception:  # noqa: BLE001 - no Qt binding: skip the icon
                icon_factory = None
        if icon_factory is not None:
            try:
                icon = icon_factory(str(path))
                app.setWindowIcon(icon)
                window.setWindowIcon(icon)
                result["icon_set"] = True
            except Exception as exc:  # noqa: BLE001 - cosmetic; never block launch
                result["icon_error"] = type(exc).__name__

    try:
        app.setApplicationName(NAME)
        app.setApplicationDisplayName(NAME)
        result["app_name_set"] = True
    except Exception as exc:  # noqa: BLE001 - older bindings may lack a setter
        result["app_name_error"] = type(exc).__name__

    try:
        app.setApplicationVersion(current_release_identity().application_version)
        result["app_version_set"] = True
    except Exception as exc:  # noqa: BLE001 - older bindings may lack a setter
        result["app_version_error"] = type(exc).__name__

    result["windows_app_id_set"] = set_windows_app_id()
    return result
