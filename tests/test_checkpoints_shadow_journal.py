"""#613 Stage 2: run checkpoints mirror into the shadow journal.

``vestahub/checkpoints.py`` is Stage 1's ``operations`` entry -- "operations:
run checkpoints" -- and the first non-lease table migrated, which is what
makes it worth its own suite: it demonstrates the shared helper generalises
past the shape it was extracted from.

The helper's own behaviour is covered once in ``test_shadow_journal.py``.
These tests cover the *wiring*: that every checkpoint transition reaches the
mirror, that the dual-read reports a real divergence, and that the journal
cannot disturb ``list_run_checkpoints``.

That last one is not hypothetical here. ``list_run_checkpoints`` globs its
directory non-recursively for ``*.json`` -- the same shape that broke
``worktree_leases`` -- but it also swallows per-file parse errors and
``continue``s, so a sibling journal would not have raised. It would have been
counted as a checkpoint that silently failed to load, which is worse.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestahub.checkpoints import (
    checkpoint_contradiction_report,
    create_run_checkpoint,
    finalize_run_checkpoint,
    list_run_checkpoints,
    load_run_checkpoint,
    shadow_journal_projection,
)


class CheckpointShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _create(self, checkpoint_id: str = "cp-1"):
        return create_run_checkpoint(
            self.root,
            checkpoint_id=checkpoint_id,
            task_id="task-a",
            task="do the thing",
            edit_capable=True,
            mode="safe-auto",
            model="account:claude:sonnet",
            read_budget=False,
        )

    def test_creation_is_mirrored_and_agrees_with_the_file(self):
        created = self._create()

        self.assertEqual(
            shadow_journal_projection(self.root, created.checkpoint_id),
            created.to_dict(),
        )
        self.assertIsNone(
            checkpoint_contradiction_report(self.root, created.checkpoint_id)
        )

    def test_finalize_is_mirrored_and_agrees_with_the_file(self):
        created = self._create()

        finalized = finalize_run_checkpoint(
            self.root,
            created.checkpoint_id,
            completion_state="answered",
            outcome="done",
            changed_files=("src/app.py",),
        )

        self.assertEqual(finalized.completion_state, "answered")
        self.assertEqual(
            shadow_journal_projection(self.root, created.checkpoint_id),
            finalized.to_dict(),
        )
        self.assertIsNone(
            checkpoint_contradiction_report(self.root, created.checkpoint_id)
        )

    def test_an_idempotent_second_finalize_leaves_both_sides_agreeing(self):
        created = self._create()
        finalize_run_checkpoint(
            self.root,
            created.checkpoint_id,
            completion_state="answered",
            outcome="done",
            changed_files=(),
        )
        before = shadow_journal_projection(self.root, created.checkpoint_id)

        again = finalize_run_checkpoint(
            self.root,
            created.checkpoint_id,
            completion_state="failed",
            outcome="should not overwrite",
            changed_files=(),
        )

        # A terminal checkpoint is immutable: neither side moved.
        self.assertEqual(again.completion_state, "answered")
        self.assertEqual(
            shadow_journal_projection(self.root, created.checkpoint_id), before
        )
        self.assertIsNone(
            checkpoint_contradiction_report(self.root, created.checkpoint_id)
        )

    def test_the_journal_never_appears_in_the_checkpoint_listing(self):
        """Regression pin, and a worse failure mode than worktree_leases had.

        ``list_run_checkpoints`` swallows per-file parse errors, so a sibling
        journal would have been silently skipped rather than raising -- an
        invisible defect instead of a loud one.
        """
        first = self._create("cp-1")
        self._create("cp-2")
        finalize_run_checkpoint(
            self.root,
            first.checkpoint_id,
            completion_state="answered",
            outcome="done",
            changed_files=(),
        )

        listed = sorted(item.checkpoint_id for item in list_run_checkpoints(self.root))

        self.assertEqual(listed, ["cp-1", "cp-2"])

    def test_an_out_of_band_write_is_reported(self):
        created = self._create()
        path = self.root / ".vestahub" / "agent" / "checkpoints" / "cp-1.json"
        tampered = {**created.to_dict(), "completion_state": "failed", "outcome": "x"}
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = checkpoint_contradiction_report(self.root, created.checkpoint_id)

        self.assertIsNotNone(report)
        self.assertEqual(report["checkpoint_id"], "cp-1")
        self.assertIn("completion_state", report["mismatched_fields"])

    def test_a_never_created_checkpoint_agrees_as_both_empty(self):
        self.assertIsNone(checkpoint_contradiction_report(self.root, "never"))
        self.assertEqual(shadow_journal_projection(self.root, "never"), {})

    def test_the_legacy_loader_is_unaffected_by_the_mirror(self):
        created = self._create()

        self.assertEqual(
            load_run_checkpoint(self.root, created.checkpoint_id).to_dict(),
            created.to_dict(),
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
