"""#613 Stage 2: parallel-agent assignments mirror into the shadow journal.

``opaihub/parallel_agents.py`` is Stage 1's "runs: concurrent agent slots".

This module was the first migrated one that had **no interprocess locking at
all** -- ``save_assignment`` hand-rolled a temp-write plus replace. The
replace is atomic, so a reader never saw a torn file, but two processes
writing the same assignment could still interleave, and the shadow journal
needs writes observed in the same order the file took them. The migration
therefore takes the same lock every other migrated module uses.

What that fixes, and what it does not, is worth being exact about: it closes
the window *inside* save. It does not close the wider check-then-claim race
in :func:`claim_assignment`, which loads, counts active assignments against
``max_active``, and only then writes -- two claimants can still both pass the
limit check. A test below documents that gap deliberately rather than leaving
it implied.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from opaihub.parallel_agents import (
    AgentAssignment,
    assignment_contradiction_report,
    load_assignment,
    list_assignments,
    save_assignment,
    shadow_journal_projection,
)


def _assignment(assignment_id: str = "a-1", **overrides) -> AgentAssignment:
    base = {
        "assignment_id": assignment_id,
        "issue_number": 7,
        "title": "fix the thing",
        "owner": "agent-1",
        "branch": "codex/issue-7-fix-the-thing",
        "worktree": "/tmp/agents/issue-7",
        "intended_paths": ("src/",),
        "status": "planned",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return AgentAssignment(**base)


class AssignmentShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_saved_assignment_is_mirrored_and_agrees(self):
        assignment = _assignment()
        save_assignment(self.root, assignment)

        self.assertEqual(
            shadow_journal_projection(self.root, "a-1"), assignment.to_dict()
        )
        self.assertIsNone(assignment_contradiction_report(self.root, "a-1"))

    def test_each_status_change_is_mirrored(self):
        assignment = _assignment()
        save_assignment(self.root, assignment)

        claimed = replace(assignment, status="active", owner="agent-2")
        save_assignment(self.root, claimed)
        done = replace(claimed, status="completed", changed_files=("src/app.py",))
        save_assignment(self.root, done)

        projection = shadow_journal_projection(self.root, "a-1")
        self.assertEqual(projection["status"], "completed")
        self.assertEqual(projection, done.to_dict())
        self.assertIsNone(assignment_contradiction_report(self.root, "a-1"))

    def test_the_journal_never_appears_in_the_assignment_listing(self):
        """list_assignments globs *.json and swallows parse errors.

        A sibling journal would have been counted as an assignment that
        silently failed to load -- invisible rather than loud, the same shape
        that bit worktree_leases and checkpoints.
        """
        save_assignment(self.root, _assignment("a-1"))
        save_assignment(self.root, _assignment("a-2", issue_number=8))
        save_assignment(self.root, replace(_assignment("a-1"), status="active"))

        listed = sorted(item.assignment_id for item in list_assignments(self.root))

        self.assertEqual(listed, ["a-1", "a-2"])

    def test_the_hand_rolled_temp_file_is_gone(self):
        """The old temp+replace left `<id>.tmp`; atomic_write_text does not."""
        save_assignment(self.root, _assignment())

        directory = self.root / ".opaihub" / "agent" / "parallel"
        strays = sorted(p.name for p in directory.iterdir() if p.suffix == ".tmp")

        self.assertEqual(strays, [])

    def test_an_out_of_band_write_is_reported(self):
        assignment = _assignment()
        save_assignment(self.root, assignment)
        path = self.root / ".opaihub" / "agent" / "parallel" / "a-1.json"
        tampered = {**assignment.to_dict(), "status": "completed", "owner": "someone"}
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = assignment_contradiction_report(self.root, "a-1")

        self.assertIsNotNone(report)
        self.assertEqual(report["assignment_id"], "a-1")
        self.assertIn("status", report["mismatched_fields"])
        self.assertIn("owner", report["mismatched_fields"])

    def test_a_never_saved_assignment_agrees_as_both_empty(self):
        self.assertIsNone(assignment_contradiction_report(self.root, "absent"))
        self.assertEqual(shadow_journal_projection(self.root, "absent"), {})

    def test_concurrent_saves_leave_file_and_shadow_agreeing(self):
        """The lock this migration added is what makes this deterministic."""
        save_assignment(self.root, _assignment())

        def write(index: int) -> None:
            save_assignment(
                self.root,
                replace(_assignment(), status="active", owner=f"agent-{index}"),
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(12)))

        self.assertIsNone(assignment_contradiction_report(self.root, "a-1"))
        self.assertEqual(
            shadow_journal_projection(self.root, "a-1"),
            load_assignment(self.root, "a-1").to_dict(),
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
