"""Which OPai is running, and who is allowed to update it (#832 scope item 3).

Two questions that looked like one.

`load_installed_build` reports `version` from release-identity.json when that
file exists and from the imported `__version__` when it does not. Those are
different clocks -- the file is on disk and an update rewrites it, while the
imported value is whatever this process loaded at startup. Reproduced before
the fix:

    running code version : 0.2.1a1
    status() reports     : 0.9.9 / newbuild

which is "Update complete" over code that is not running: the one claim an
updater must never make.

And ownership: a source checkout updates itself from origin/main, but a pip or
pipx installation must not. Fast-forwarding a git tree that no longer backs the
running code is how an updater "succeeds" against the wrong installation.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from opai.update.models import InstallType  # noqa: E402
from opai.update.ownership import (  # noqa: E402
    describe_ownership,
    running_identity,
    safe_launcher_identity,
)


class RunningVersusDiskIdentityTests(unittest.TestCase):
    def test_the_disk_can_report_a_version_this_process_is_not_running(self) -> None:
        # The defect, kept as the reason the two are reported separately.
        import tempfile

        from opai.update.factory import load_installed_build
        import opai

        path = Path(tempfile.mkdtemp()) / "release-identity.json"
        path.write_text(
            json.dumps(
                {
                    "version": "0.9.9",
                    "build_id": "newbuild",
                    "channel": "stable",
                    "platform": "windows",
                    "architecture": "x86_64",
                    "install_type": "portable",
                }
            ),
            encoding="utf-8",
        )

        built = load_installed_build(identity_paths=[path])

        self.assertEqual(built.version, "0.9.9")
        self.assertNotEqual(built.version, opai.__version__)

    def test_running_identity_reports_what_this_process_loaded(self) -> None:
        import opai

        self.assertEqual(running_identity()["version"], opai.__version__)


class OwnershipDetectionTests(unittest.TestCase):
    def test_a_source_checkout_owns_its_own_updates(self) -> None:
        ownership = describe_ownership(InstallType.SOURCE_CHECKOUT)
        self.assertEqual(ownership.owner, "opai")
        self.assertEqual(ownership.mechanism, "git")
        self.assertTrue(ownership.self_updatable)

    def test_a_managed_installation_is_never_self_updatable(self) -> None:
        # Whatever else is true of the machine, an administrator's policy wins.
        ownership = describe_ownership(
            InstallType.SOURCE_CHECKOUT, management_source="Acme MDM"
        )
        self.assertEqual(ownership.owner, "external")
        self.assertFalse(ownership.self_updatable)
        self.assertIn("administrator", ownership.remediation)

    def test_a_pipx_installation_names_pipx_and_its_command(self) -> None:
        # Detected from the interpreter's own path, because pipx installs
        # through pip and so writes "pip" into INSTALLER.
        with mock.patch(
            "opai.update.ownership._interpreter_path",
            return_value="/home/u/.local/pipx/venvs/opai/bin/python",
        ):
            ownership = describe_ownership(InstallType.PORTABLE)

        self.assertEqual(ownership.owner, "pipx")
        self.assertIn("pipx upgrade", ownership.remediation)
        self.assertFalse(ownership.self_updatable)

    def test_a_homebrew_installation_names_brew(self) -> None:
        with mock.patch(
            "opai.update.ownership._interpreter_path",
            return_value="/opt/homebrew/opt/python/bin/python3",
        ):
            ownership = describe_ownership(InstallType.PORTABLE)

        self.assertEqual(ownership.owner, "homebrew")
        self.assertIn("brew upgrade", ownership.remediation)

    def test_a_pip_installation_names_pip(self) -> None:
        with (
            mock.patch(
                "opai.update.ownership._interpreter_path",
                return_value="/usr/bin/python3",
            ),
            mock.patch(
                "opai.update.ownership._distribution_installer", return_value="pip"
            ),
        ):
            ownership = describe_ownership(InstallType.PORTABLE)

        self.assertEqual(ownership.owner, "pip")
        self.assertIn("pip install --upgrade", ownership.remediation)
        self.assertFalse(ownership.self_updatable)

    def test_an_installation_nobody_claims_says_so_rather_than_guessing(self) -> None:
        with (
            mock.patch(
                "opai.update.ownership._interpreter_path",
                return_value="/usr/bin/python3",
            ),
            mock.patch(
                "opai.update.ownership._distribution_installer", return_value=""
            ),
        ):
            ownership = describe_ownership(InstallType.PORTABLE)

        self.assertEqual(ownership.owner, "unknown")
        self.assertFalse(ownership.self_updatable)

    def test_only_opai_owned_installations_are_self_updatable(self) -> None:
        # The invariant that stops the updater acting on an installation some
        # other tool is responsible for.
        for install_type in (InstallType.PORTABLE, InstallType.WINDOWS_MSIX):
            with self.subTest(install_type=install_type):
                self.assertFalse(describe_ownership(install_type).self_updatable)


class LauncherIdentityTests(unittest.TestCase):
    def test_the_launcher_is_named_without_naming_the_user(self) -> None:
        # Updater diagnostics get pasted into issues. The part that identifies
        # the installation is kept; the part that identifies the person is not.
        identity = safe_launcher_identity()
        self.assertTrue(identity)
        home = str(Path.home())
        self.assertNotIn(home, identity)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
