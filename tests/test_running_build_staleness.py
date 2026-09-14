"""The updater has to notice when the process outlives the code it loaded.

`check()` asks whether the checkout is behind its remote. After an update it
correctly answers no -- while the window in front of the user is still running
whatever it loaded at launch. Nothing compared those two, so a session could
sit for hours showing a UI several merges old with every surface agreeing there
was nothing to update. The check was reporting on the repository; what the user
sees is the process.

These tests pin the comparison and, just as importantly, pin it not firing:
a hint that cries wolf on a touched file or a reinstall of identical bytes
would be worse than no hint, because it asks for a restart that changes
nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from opai.update import running_build


def _fake_manifest(fingerprint: str):
    return lambda root: {"fingerprint_sha256": fingerprint, "asset_count": 1}


class RunningBuildStalenessTests(unittest.TestCase):
    def setUp(self) -> None:
        running_build.reset_for_tests()
        self.addCleanup(running_build.reset_for_tests)
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "app.js").write_text("original\n", encoding="utf-8")

    def _touch_newer(self, name: str = "app.js") -> None:
        path = self.root / name
        stat = path.stat()
        os.utime(path, (stat.st_atime + 60, stat.st_mtime + 60))

    def test_without_a_baseline_nothing_is_stale(self) -> None:
        # prime() has not run, so there is nothing to compare against and the
        # honest answer is "no idea", never "yes".
        self.assertFalse(running_build.running_build_is_stale(self.root))

    def test_an_unchanged_build_is_not_stale(self) -> None:
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
            self.assertFalse(running_build.running_build_is_stale(self.root))

    def test_changed_bytes_on_disk_are_stale(self) -> None:
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
        # The mtime gate has to open before the fingerprint is even consulted.
        self._touch_newer()
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("bbb")):
            self.assertTrue(running_build.running_build_is_stale(self.root))

    def test_a_touched_file_with_identical_bytes_is_not_stale(self) -> None:
        # A checkout that rewrites a file to what it already said, or a
        # reinstall that copies identical bytes, moves the mtime and changes
        # nothing. Asking for a restart there is asking for nothing.
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
            self._touch_newer()
            self.assertFalse(running_build.running_build_is_stale(self.root))

    def test_the_runtime_index_never_counts_as_a_change(self) -> None:
        # It is rewritten on every launch for cache-busting, so it is always
        # newer than the process and would report staleness forever.
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
            index = self.root / "web"
            index.mkdir()
            (index / ".runtime-index.html").write_text("<html>", encoding="utf-8")
            os.utime(
                index / ".runtime-index.html",
                (10**10, 10**10),
            )
            self.assertFalse(running_build.running_build_is_stale(self.root))

    def test_priming_twice_keeps_the_first_baseline(self) -> None:
        # The memory image does not change when the files do, so re-priming
        # after an update would silently adopt the new build as "what we are
        # running" and never report anything again.
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
        self._touch_newer()
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("bbb")):
            running_build.prime(self.root)
            self.assertTrue(running_build.running_build_is_stale(self.root))

    def test_unreadable_assets_are_not_reported_as_stale(self) -> None:
        # Integrity is a different subsystem's job, and its failure must not
        # surface here as a restart request.
        with mock.patch.object(running_build, "asset_manifest", _fake_manifest("aaa")):
            running_build.prime(self.root)
        self._touch_newer()
        boom = mock.Mock(side_effect=running_build.AssetIntegrityError("x", "y", "z"))
        with mock.patch.object(running_build, "asset_manifest", boom):
            self.assertFalse(running_build.running_build_is_stale(self.root))


class MaintenanceSurfacesStalenessTests(unittest.TestCase):
    """The hint has to reach a state the UI actually renders."""

    def _service(self):
        """A service over a private home, offline.

        It used the real one: a real `~/.opai` update lock, which any Vesta
        running on the machine holds -- the tests errored with
        `operation_busy` whenever the app was open -- and a real manifest
        fetch over the network.
        """

        from opai.update.factory import create_update_service

        home = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(home.cleanup)

        def offline(url: str) -> bytes:
            raise OSError("tests do not reach the update feed")

        with mock.patch("opai.gui_workspace.load_recent_workspaces", return_value=[]):
            return create_update_service(
                workspaces=[], home=Path(home.name), manifest_fetcher=offline
            )

    def test_a_stale_process_lands_in_completed_with_a_restart_message(self) -> None:
        from opai.update.models import UpdateState

        service = self._service()
        service.check(force=True)
        with mock.patch.object(
            type(service), "_running_build_is_stale", lambda self: True
        ):
            operation = service.maintain()

        # COMPLETED is "installed, not yet running" -- and the one state the
        # surface offers Restart now from.
        self.assertIs(operation.state, UpdateState.COMPLETED)
        self.assertIn("Restart", operation.safe_diagnostic)
        service.check(force=True)

    def test_a_current_process_is_left_alone(self) -> None:
        from opai.update.models import UpdateState

        service = self._service()
        service.check(force=True)
        with mock.patch.object(
            type(service), "_running_build_is_stale", lambda self: False
        ):
            operation = service.maintain()
        self.assertIsNot(operation.state, UpdateState.COMPLETED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
