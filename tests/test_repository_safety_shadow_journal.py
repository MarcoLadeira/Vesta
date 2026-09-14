"""#613 Stage 2: repository_safety's shadow journal, and where best-effort is the point.

Stage 1 named ``opaihub/repository_safety.py`` JOURNAL_OWNED -- "operations:
repository mutation guards". A handle records the repository identity and
dirty state captured before Vesta is allowed to mutate anything, so it is the
record that decides whether a mutation may proceed at all.

Unlike most of the modules migrated so far this one arrived already correct:
one write site, already atomic, already under an interprocess lock, no glob
anywhere near its directory. Nothing needed fixing, which makes the only
interesting decision the one about failure.

``save_repository_handle`` fails *closed* -- its docstring says "inability to
save is a safety failure" and it raises ``RepositorySafetyPersistenceError``.
The tempting symmetry is to make the mirror fail closed too. That would be
wrong. During Stage 2 the file is authoritative and the journal is a shadow
being proven; a journal that could not be appended is a lost shadow, not a
lost safety guarantee. Failing the save on it would turn a migration detail
into a brand-new way for the safety gate to refuse work the user asked for.
The last test here pins exactly that.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub import run_journal
from opaihub.repository_safety import (
    capture_repository_handle,
    load_repository_handle,
    repository_handle_contradiction_report,
    repository_handle_projection,
    save_repository_handle,
)


class _HandleFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        make_repo(self.root, files={"src/app.py": "print('ok')\n"}, commit=True)

    def _capture(self, *, task_id: str = "task-a", run_id: str = "run-a"):
        return capture_repository_handle(self.root, task_id=task_id, run_id=run_id)

    def _save(self, **kwargs):
        handle = self._capture(**kwargs)
        path = save_repository_handle(self.root, handle)
        return handle, path


class ShadowMirrorsSavedHandlesTests(_HandleFixture):
    def test_saving_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        handle, _path = self._save()

        self.assertEqual(
            repository_handle_projection(self.root, handle.handle_id),
            handle.to_dict(),
        )
        self.assertIsNone(
            repository_handle_contradiction_report(self.root, handle.handle_id)
        )

    def test_the_mirrored_handle_is_the_one_the_loader_returns(self):
        handle, _path = self._save()

        loaded = load_repository_handle(self.root, handle.handle_id)

        self.assertEqual(
            repository_handle_projection(self.root, handle.handle_id),
            loaded.to_dict(),
        )

    def test_re_saving_the_same_handle_moves_both_sides_together(self):
        handle, _path = self._save()
        (self.root / "src" / "app.py").write_text(
            "print('changed')\n", encoding="utf-8"
        )
        again = capture_repository_handle(self.root, task_id="task-a", run_id="run-a")
        save_repository_handle(self.root, again)

        self.assertIsNone(
            repository_handle_contradiction_report(self.root, again.handle_id)
        )
        self.assertEqual(
            repository_handle_projection(self.root, again.handle_id),
            again.to_dict(),
        )

    def test_separate_runs_keep_separate_shadows(self):
        first, _ = self._save(run_id="run-a")
        second, _ = self._save(run_id="run-b")

        if first.handle_id == second.handle_id:
            self.skipTest("handle identity does not distinguish these runs")
        self.assertEqual(
            repository_handle_projection(self.root, first.handle_id),
            first.to_dict(),
        )
        self.assertEqual(
            repository_handle_projection(self.root, second.handle_id),
            second.to_dict(),
        )


class ContradictionReportIsExactTests(_HandleFixture):
    def test_an_out_of_band_file_write_is_reported(self):
        """The scenario #613 exists for: something rewrote the guard record."""

        handle, path = self._save()
        tampered = {**handle.to_dict(), "task_id": "somebody-elses-task"}
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = repository_handle_contradiction_report(self.root, handle.handle_id)

        self.assertIsNotNone(report)
        self.assertIn("task_id", report["mismatched_fields"])
        self.assertEqual(report["handle_id"], handle.handle_id)

    def test_a_lost_handle_file_is_reported_against_a_surviving_shadow(self):
        """A mutation guard that vanished is precisely what #613 wants to see."""

        handle, path = self._save()
        path.unlink()

        report = repository_handle_contradiction_report(self.root, handle.handle_id)

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"], handle.to_dict())

    def test_a_never_saved_handle_agrees_as_both_empty(self):
        self.assertIsNone(
            repository_handle_contradiction_report(self.root, "never-saved")
        )
        self.assertEqual(repository_handle_projection(self.root, "never-saved"), {})


class ShadowFailureMustNotBecomeASafetyFailureTests(_HandleFixture):
    """The one real decision in this migration.

    ``save_repository_handle`` fails closed by design. The mirror deliberately
    does not inherit that, because the file is still authoritative in Stage 2 --
    so a broken journal append must leave the save succeeding and the guard
    intact.
    """

    def test_a_broken_journal_append_does_not_fail_the_save(self):
        handle = self._capture()

        with mock.patch.object(run_journal, "append", side_effect=OSError("disk full")):
            path = save_repository_handle(self.root, handle)

        self.assertTrue(path.exists())
        self.assertEqual(
            load_repository_handle(self.root, handle.handle_id).to_dict(),
            handle.to_dict(),
        )

    def test_the_next_healthy_save_repairs_the_shadow(self):
        """A lost append must not poison the journal for good."""

        handle = self._capture()
        with mock.patch.object(run_journal, "append", side_effect=OSError("disk full")):
            save_repository_handle(self.root, handle)

        save_repository_handle(self.root, handle)

        self.assertEqual(
            repository_handle_projection(self.root, handle.handle_id),
            handle.to_dict(),
        )
        self.assertIsNone(
            repository_handle_contradiction_report(self.root, handle.handle_id)
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
