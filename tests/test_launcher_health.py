"""#818: the app that is broken and does not know it.

OPai's desktop icon runs a launcher pip generated at install time, and that
launcher hard-codes the absolute path of the interpreter it will spawn. On a
real machine that path was ``C:\\Python313\\pythonww.exe`` -- a file that does
not exist -- because pip's vendored distlib derives a windowed interpreter by
substring substitution (``fn.replace("python", "pythonw")``) and OPai's own
updater had reinstalled itself from inside the GUI, where ``sys.executable``
is already ``pythonw.exe``.

A Windows launcher whose interpreter is missing exits 1 with no window, no
dialog, no stderr and no log. Every other surface kept working, so
``opai doctor`` reported ``ready`` while the icon the user actually clicks did
nothing whatsoever.

These tests pin both halves: the interpreter OPai hands to pip, and doctor's
refusal to call an install healthy without having read its launchers.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from opaihub import launcher_health, proc


# The exact substitution pip's vendored distlib applies to build a gui_scripts
# launcher (pip/_vendor/distlib/scripts.py, _get_alternate_executable). It is
# reproduced rather than imported so this test states the rule it depends on.
def _distlib_windowed(executable: str) -> str:
    directory, name = os.path.split(executable)
    return os.path.join(directory, name.replace("python", "pythonw"))


# The real OPai-Desktop.exe carries a literal "#!" inside its launcher stub,
# 37KB before the shebang that matters -- a whole-file search finds that one
# and reports an interpreter nobody will ever run. The decoys below are that
# trap, planted at both distances a reader can get wrong.
_FAR_DECOY = b"#!C:\\stub\\NOT-AN-INTERPRETER.exe\n"
_NEAR_DECOY = b"#!C:\\stub\\ALSO-NOT-IT.exe\n"


def _windows_launcher(interpreter: str, *, noise: bool = True) -> bytes:
    """Bytes shaped like a pip launcher: stub, shebang, appended zip.

    pip writes the real shebang immediately before the appended zip, so that
    is the only place worth reading. With ``noise`` the fixture plants a decoy
    far away -- which a whole-file search reaches first -- and a second one
    close enough to fall inside the search window, which a forward search
    reaches first. Only "the last ``#!`` in a small window back from the zip"
    answers both correctly.
    """

    stub = b"MZ\x90\x00" + b"\x00" * 64
    if noise:
        stub += _FAR_DECOY
        stub += "  PATHEXT ; PyLauncher STATIC".encode("utf-16-le")
        stub += b"\x00" * 4096
        stub += _NEAR_DECOY
        stub += b"\x00" * 64
    shebang = b"#!" + interpreter.encode("utf-8") + b"\n"
    return stub + shebang + b"PK\x03\x04" + b"\x00" * 32


class ConsoleInterpreterTests(unittest.TestCase):
    """What OPai must hand to `pip install`, and why."""

    def test_a_windowed_interpreter_becomes_its_console_sibling(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "python.exe").write_bytes(b"")
            windowed = str(root / "pythonw.exe")

            self.assertEqual(
                proc.console_interpreter(windowed),
                str(root / "python.exe"),
            )

    def test_the_result_survives_distlibs_substitution(self) -> None:
        """The regression itself: what pip derives must be a real file.

        This is the assertion the shipped bug fails. Feeding distlib
        ``pythonw.exe`` yields ``pythonww.exe`` and a dead desktop icon.
        """
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "python.exe").write_bytes(b"")
            (root / "pythonw.exe").write_bytes(b"")

            chosen = proc.console_interpreter(str(root / "pythonw.exe"))
            derived = _distlib_windowed(chosen)

            self.assertTrue(
                os.path.exists(derived),
                f"pip would generate a launcher running {derived}, which is not there",
            )
            self.assertEqual(Path(derived).name, "pythonw.exe")

    def test_a_console_interpreter_is_left_alone(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "python.exe").write_bytes(b"")
            plain = str(root / "python.exe")

            self.assertEqual(proc.console_interpreter(plain), plain)

    def test_no_console_sibling_means_no_substitution(self) -> None:
        """Better a suboptimal shebang than refusing to install."""

        with TemporaryDirectory() as raw:
            windowed = str(Path(raw) / "pythonw.exe")

            self.assertEqual(proc.console_interpreter(windowed), windowed)

    def test_a_versioned_windowed_name_keeps_its_version(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "python3.13.exe").write_bytes(b"")

            self.assertEqual(
                proc.console_interpreter(str(root / "pythonw3.13.exe")),
                str(root / "python3.13.exe"),
            )

    def test_an_empty_executable_is_not_invented(self) -> None:
        self.assertEqual(proc.console_interpreter(""), "")


class ReadInterpreterTests(unittest.TestCase):
    """Reading the interpreter back out of a launcher on disk."""

    def test_a_windows_launcher_reports_its_interpreter(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "OPai-Desktop.exe"
            path.write_bytes(_windows_launcher(r"C:\Python313\pythonw.exe"))

            self.assertEqual(
                launcher_health.read_interpreter(path),
                r"C:\Python313\pythonw.exe",
            )

    def test_stub_bytes_are_not_mistaken_for_the_shebang(self) -> None:
        """The trap that shipped: the real .exe has a "#!" in its stub too."""

        with TemporaryDirectory() as raw:
            path = Path(raw) / "OPai-Desktop.exe"
            blob = _windows_launcher(r"C:\Python313\pythonww.exe")
            path.write_bytes(blob)

            # The fixture really is adversarial: both decoys are in the bytes.
            self.assertIn(_FAR_DECOY, blob)
            self.assertIn(_NEAR_DECOY, blob)
            self.assertGreater(blob.count(b"#!"), 2)

            self.assertEqual(
                launcher_health.read_interpreter(path),
                r"C:\Python313\pythonww.exe",
            )

    def test_the_decoy_nearest_the_zip_still_loses(self) -> None:
        """Reading forward from the window start picks up the wrong one."""

        with TemporaryDirectory() as raw:
            path = Path(raw) / "OPai-Desktop.exe"
            path.write_bytes(_windows_launcher(r"C:\Python313\python.exe"))

            found = launcher_health.read_interpreter(path)

            self.assertEqual(found, r"C:\Python313\python.exe")
            self.assertNotIn("NOT-AN-INTERPRETER", found)
            self.assertNotIn("ALSO-NOT-IT", found)

    def test_a_quoted_path_loses_its_quotes(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "opai.exe"
            path.write_bytes(_windows_launcher(r'"C:\Program Files\Py\python.exe"'))

            self.assertEqual(
                launcher_health.read_interpreter(path),
                r"C:\Program Files\Py\python.exe",
            )

    def test_a_posix_script_reports_its_shebang(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "opai"
            path.write_bytes(b"#!/usr/bin/python3\nprint(1)\n")

            self.assertEqual(launcher_health.read_interpreter(path), "/usr/bin/python3")

    def test_bytes_with_no_interpreter_read_as_nothing(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "opai.exe"
            path.write_bytes(b"\x00" * 500)

            self.assertEqual(launcher_health.read_interpreter(path), "")


def _read_posix(blob: bytes) -> str:
    with TemporaryDirectory() as raw:
        path = Path(raw) / "opai"
        path.write_bytes(blob)
        return launcher_health.read_interpreter(path)


class AShebangIsACommandLineTests(unittest.TestCase):
    """#818 review finding 17: the first word of a shebang is not always it."""

    def test_arguments_are_not_part_of_the_path(self) -> None:
        # Checked whole, a good interpreter was reported missing.
        self.assertEqual(_read_posix(b"#!/usr/bin/python3 -E\n"), "/usr/bin/python3")

    def test_env_means_whatever_is_on_path(self) -> None:
        with mock.patch.object(
            launcher_health.shutil, "which", return_value="/usr/local/bin/python3"
        ):
            found = _read_posix(b"#!/usr/bin/env python3\n")

        self.assertEqual(found, "/usr/local/bin/python3")

    def test_env_that_finds_nothing_is_not_healthy(self) -> None:
        with mock.patch.object(launcher_health.shutil, "which", return_value=None):
            found = _read_posix(b"#!/usr/bin/env python3\n")

        # The bare name, which then fails the existence check -- env would not
        # find it either.
        self.assertEqual(found, "python3")
        self.assertFalse(os.path.exists(found))

    def test_a_trampoline_reports_the_interpreter_it_execs(self) -> None:
        # pip's launcher for interpreter paths too long for a shebang line.
        blob = (
            b"#!/bin/sh\n"
            b'\'\'\'exec\' "/opt/very long/venv/bin/python" "$0" "$@"\n'
            b"' '''\n"
            b"from opai.cli import main\n"
        )

        self.assertEqual(_read_posix(blob), "/opt/very long/venv/bin/python")

    def test_a_shell_launcher_with_no_exec_line_is_unreadable(self) -> None:
        # /bin/sh always exists; reporting it would call this healthy.
        self.assertEqual(_read_posix(b"#!/bin/sh\necho hello\n"), "")


class InspectLauncherTests(unittest.TestCase):
    """The verdict for a whole installation."""

    def _install(self, directory: Path, name: str, interpreter: str) -> None:
        suffix = ".exe" if os.name == "nt" else ""
        (directory / (name + suffix)).write_bytes(_windows_launcher(interpreter))

    def test_a_missing_interpreter_is_named_not_glossed(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            gone = str(root / "pythonww.exe")
            self._install(root, "OPai-Desktop", gone)

            reports = launcher_health.inspect_launchers(
                directories=[root],
            )
            found = {report.name: report for report in reports}
            self.assertIn("OPai-Desktop", found, "entry points not discovered")
            desktop = found["OPai-Desktop"]

            self.assertEqual(desktop.status, launcher_health.MISSING_INTERPRETER)
            self.assertFalse(desktop.healthy)
            self.assertIn("does not exist", desktop.describe())
            self.assertIn("exit silently", desktop.describe())

    def test_a_present_interpreter_is_healthy(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            real = root / "python.exe"
            real.write_bytes(b"")
            self._install(root, "OPai-Desktop", str(real))

            reports = launcher_health.inspect_launchers(directories=[root])
            desktop = {r.name: r for r in reports}["OPai-Desktop"]

            self.assertEqual(desktop.status, launcher_health.OK)
            self.assertTrue(desktop.healthy)

    def test_an_absent_launcher_is_not_installed_rather_than_broken(self) -> None:
        with TemporaryDirectory() as raw:
            reports = launcher_health.inspect_launchers(directories=[Path(raw)])

            self.assertTrue(reports, "no entry points read from the distribution")
            self.assertTrue(
                all(r.status == launcher_health.NOT_INSTALLED for r in reports)
            )
            self.assertEqual(launcher_health.broken_launchers(reports), [])

    def test_unreadable_is_never_reported_as_healthy(self) -> None:
        """ "I could not read it" and "it is fine" are different answers."""

        with TemporaryDirectory() as raw:
            root = Path(raw)
            suffix = ".exe" if os.name == "nt" else ""
            (root / ("OPai-Desktop" + suffix)).write_bytes(b"\x00" * 500)

            reports = launcher_health.inspect_launchers(directories=[root])
            desktop = {r.name: r for r in reports}["OPai-Desktop"]
            self.assertEqual(desktop.status, launcher_health.UNREADABLE)

            summary = launcher_health.summary(reports)
            self.assertFalse(summary["healthy"])
            self.assertIn("OPai-Desktop", summary["unreadable"])

    def test_summary_of_an_uncheckable_install_is_not_available(self) -> None:
        summary = launcher_health.summary([])

        self.assertFalse(summary["available"])
        self.assertFalse(summary["healthy"])


class DoctorVerdictTests(unittest.TestCase):
    """A dead icon has to reach doctor's top line."""

    def test_a_broken_launcher_needs_attention(self) -> None:
        from opai import cli

        self.assertTrue(
            cli._launchers_need_attention(
                {"available": True, "broken": ["OPai-Desktop"]}
            )
        )

    def test_a_healthy_install_does_not(self) -> None:
        from opai import cli

        self.assertFalse(
            cli._launchers_need_attention({"available": True, "broken": []})
        )

    def test_an_uncheckable_install_is_not_called_broken(self) -> None:
        """Reported as unavailable, not turned into a red verdict."""

        from opai import cli

        self.assertFalse(
            cli._launchers_need_attention(
                {"available": False, "healthy": False, "broken": []}
            )
        )

    def test_doctor_reports_what_the_launchers_will_run(self) -> None:
        from opai import cli

        payload = cli._launcher_doctor()

        self.assertIn("available", payload)
        self.assertIn("launchers", payload)
        if payload.get("available"):
            for entry in payload["launchers"]:
                self.assertIn(entry["status"], launcher_health.STATUSES)
                self.assertTrue(entry["detail"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
