"""A workspace with no repository in it is a workspace, not a safety incident.

Every edit-capable turn used to demand a Git worktree up front. In a plain
folder -- a synced drive, a scratch directory, a project not yet under version
control -- that produced:

    Vesta could not establish and persist a fresh repository identity for this
    edit-capable run. No provider was allowed to mutate the workspace. Inspect
    the repository and retry.

about a repository that did not exist. Because Bypass permissions makes every
non-discovery turn edit-capable, it refused *every* message in such a folder,
including ones that only asked for code to read. Nothing pinned that behaviour,
which is how it survived.

The distinction these tests hold in place is between "there is no repository
here" (ordinary; proceed, with the lost protection stated) and "a repository
exists and cannot be read" (an anomaly; still refuse).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestahub.provider_tools import RepositoryToolExecutor
from vestahub.repository_safety import (
    RepositoryProbeError,
    capture_repository_handle,
)


class PlainFolderProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "OneDrive - College"
        self.folder.mkdir(parents=True)
        (self.folder / "notes.py").write_text("print('hi')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_folder_without_git_reports_its_own_reason(self) -> None:
        # Not "probe_unavailable". That code means a repository exists and
        # could not be read, which is a fault; this is not one, and callers
        # have to be able to tell them apart.
        with self.assertRaises(RepositoryProbeError) as caught:
            capture_repository_handle(self.folder, task_id="t", run_id="r")
        self.assertEqual(caught.exception.reason, "not_a_repository")


class PlainFolderMutationGateTests(unittest.TestCase):
    """Writes proceed; Git operations refuse, and say why."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "workspace"
        self.folder.mkdir(parents=True)
        (self.folder / "notes.py").write_text("print('hi')\n", encoding="utf-8")
        self.executor = RepositoryToolExecutor(
            self.folder, allow_edits=True, autonomy="bypass"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_absence_of_a_repository_is_not_recorded_as_a_safety_error(
        self,
    ) -> None:
        self.assertTrue(self.executor._repository_absent)
        self.assertEqual(self.executor._repository_safety_error, "")

    def test_a_file_write_is_permitted_without_a_repository(self) -> None:
        # The regression this file exists for: the gate failed closed on a
        # missing handle, so every write in a plain folder was refused.
        self.assertIsNone(
            self.executor._mutation_gate("write_file", ("notes.py",)),
        )

    def test_applying_a_patch_is_permitted_without_a_repository(self) -> None:
        self.assertIsNone(self.executor._mutation_gate("apply_patch", ("notes.py",)))

    def test_git_operations_are_refused_with_an_actionable_reason(self) -> None:
        for operation in ("git_commit", "git_create_branch", "git_push"):
            with self.subTest(operation=operation):
                blocked = self.executor._mutation_gate(operation, ())
                self.assertIsNotNone(blocked)
                assert blocked is not None
                self.assertEqual(blocked["data"]["reason"], "not_a_repository")
                # It must name the folder as the reason and `git init` as the
                # fix, rather than send the user to inspect a repository that
                # is not there.
                self.assertIn("git init", blocked["message"])
                self.assertNotIn("Repository identity", blocked["message"])


class RealProbeFailureStillBlocksTests(unittest.TestCase):
    """The relaxation must not become "any repository problem is fine"."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "workspace"
        self.folder.mkdir(parents=True)
        (self.folder / "notes.py").write_text("print('hi')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_probe_failure_that_is_not_absence_still_fails_closed(self) -> None:
        executor = RepositoryToolExecutor(
            self.folder, allow_edits=True, autonomy="bypass"
        )
        # Simulate a repository that exists but whose identity could not be
        # established: absent is False, and the error is set.
        executor._repository_absent = False
        executor._repository_handle = None
        executor._repository_safety_error = "probe_unavailable"

        blocked = executor._mutation_gate("write_file", ("notes.py",))
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertEqual(blocked["data"]["reason"], "probe_unavailable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
