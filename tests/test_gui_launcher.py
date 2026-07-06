"""Windowed GUI entry point + packaged app icon (#148).

All hermetic: no Qt application, no display, no subprocess. The Qt-facing work
is exercised through the injectable ``icon_factory`` and fake app/window
recorders, so these run anywhere CI does.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from opai import brand
from opai.gui_identity import apply_window_identity, set_windows_app_id

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Recorder:
    """Minimal stand-in for a QApplication / QMainWindow."""

    def __init__(self) -> None:
        self.icons: list = []
        self.app_name = None
        self.display_name = None

    def setWindowIcon(self, icon) -> None:  # noqa: N802 - Qt signature
        self.icons.append(icon)

    def setApplicationName(self, name) -> None:  # noqa: N802 - Qt signature
        self.app_name = name

    def setApplicationDisplayName(self, name) -> None:  # noqa: N802 - Qt signature
        self.display_name = name


class AppIconTests(unittest.TestCase):
    def test_app_icon_path_resolves_packaged_png(self):
        path = brand.app_icon_path()
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "opai-icon.png")

    def test_app_icon_is_a_square_png(self):
        import struct

        path = brand.app_icon_path()
        header = path.read_bytes()[:24]
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", header[16:24])
        self.assertEqual(width, height)  # square icon, not the rectangular mascot
        self.assertGreaterEqual(width, 128)

    def test_missing_icon_degrades_to_none(self):
        with mock.patch(
            "opai.brand._resources.files",
            side_effect=FileNotFoundError,
        ):
            self.assertIsNone(brand.app_icon_path())


class WindowIdentityTests(unittest.TestCase):
    def test_icon_applied_to_both_app_and_window(self):
        app, window = _Recorder(), _Recorder()
        built: list[str] = []

        def fake_icon(path: str):
            built.append(path)
            return f"icon::{path}"

        result = apply_window_identity(app, window, icon_factory=fake_icon)

        self.assertTrue(result["icon_set"])
        self.assertTrue(result["app_name_set"])
        self.assertEqual(len(built), 1)
        self.assertEqual(app.icons, window.icons)
        self.assertEqual(app.icons[0], f"icon::{built[0]}")
        self.assertEqual(app.app_name, brand.NAME)
        self.assertEqual(app.display_name, brand.NAME)

    def test_missing_icon_still_sets_name_and_never_raises(self):
        app, window = _Recorder(), _Recorder()
        with mock.patch("opai.gui_identity.app_icon_path", return_value=None):
            result = apply_window_identity(app, window, icon_factory=lambda p: p)
        self.assertFalse(result["icon_set"])
        self.assertTrue(result["app_name_set"])
        self.assertEqual(app.icons, [])

    def test_icon_factory_failure_does_not_block_launch(self):
        app, window = _Recorder(), _Recorder()

        def boom(_path: str):
            raise RuntimeError("no Qt platform plugin")

        result = apply_window_identity(app, window, icon_factory=boom)
        self.assertFalse(result["icon_set"])
        self.assertTrue(result["app_name_set"])  # identity still applied

    def test_set_windows_app_id_returns_bool_without_raising(self):
        self.assertIsInstance(set_windows_app_id(), bool)


class EntryPointTests(unittest.TestCase):
    def test_gui_main_forwards_args_to_gui_subcommand(self):
        from opai import cli

        with mock.patch.object(cli.sys, "argv", ["opai-gui", "--project", "X"]):
            with mock.patch.object(cli, "main", return_value=0) as fake_main:
                self.assertEqual(cli.gui_main(), 0)
        fake_main.assert_called_once_with(["gui", "--project", "X"])

    def test_gui_main_propagates_exit_code(self):
        from opai import cli

        with mock.patch.object(cli.sys, "argv", ["opai-gui"]):
            with mock.patch.object(cli, "main", return_value=3):
                self.assertEqual(cli.gui_main(), 3)

    def test_gui_scripts_entry_point_declared(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        try:
            import tomllib

            data = tomllib.loads(text)
            gui_scripts = data["project"]["gui_scripts"]
            self.assertEqual(gui_scripts.get("opai-gui"), "opai.cli:gui_main")
        except ModuleNotFoundError:  # Python 3.10 has no tomllib
            self.assertIn('opai-gui = "opai.cli:gui_main"', text)

    def test_gui_main_target_is_callable(self):
        from opai import cli

        self.assertTrue(callable(cli.gui_main))


if __name__ == "__main__":
    unittest.main()
