"""#613 Stage 2: the dual read workflow_runner was missing.

``vestahub/workflow_runner.py`` is the one JOURNAL_OWNED module that already had
half of Stage 2 before #613 started. It has shadow-written every run and step
transition to a #517 journal since the beginning, and
:func:`replay_workflow_run` already rebuilds them.

What it did not have was the other half. ``replay_workflow_run``'s docstring
states the invariant -- replay "must agree with the snapshot's ``run_state``,
``reason_code``, and both the run- and step-level ``state_history``" -- and two
tests assert it on synthetic runs. Nothing checked it at runtime. Stage 4 wants
to qualify a cutover on *real* traffic, and an invariant that only holds in the
test suite cannot support that.

So this module's contribution is a comparator rather than a mirror.

Comparison is exact, timestamps included, because both sides are written from
the same ``now`` in the same transaction and are byte-identical in practice --
the existing determinism tests prove it. A looser comparison would have been
weaker than the invariant actually is, which is worth stating because the first
draft of this comparator did strip timestamps on a justification that turned
out to be wrong.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import workflow_runner
from vestahub.workflow_runner import (
    read_workflow_log,
    run_workflow,
    workflow_contradiction_report,
    workflow_journal_path,
    workflow_log_path,
)


class _CompletedCommand:
    def __init__(self, output: str) -> None:
        self.returncode = 0
        self.combined_output = output


def _synthetic_plan() -> dict[str, object]:
    return {
        "ok": True,
        "workflow": {"id": "synthetic"},
        "steps": [{"step": "synthetic", "safe_command": ["synthetic"]}],
    }


class _WorkflowFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _run(self, run_id: str = "run-a"):
        with (
            mock.patch.object(
                workflow_runner, "workflow_plan", return_value=_synthetic_plan()
            ),
            mock.patch.object(
                workflow_runner,
                "run_policy_command",
                return_value=_CompletedCommand(f"output from {run_id}"),
            ),
        ):
            return run_workflow(self.root, "synthetic", execute=True, run_id=run_id)


class AgreementIsTheNormalCaseTests(_WorkflowFixture):
    def test_a_completed_run_has_no_contradiction(self):
        self._run()

        self.assertIsNone(workflow_contradiction_report(self.root, "run-a"))

    def test_separate_runs_are_each_checked_on_their_own(self):
        self._run("run-a")
        self._run("run-b")

        self.assertIsNone(workflow_contradiction_report(self.root, "run-a"))
        self.assertIsNone(workflow_contradiction_report(self.root, "run-b"))

    def test_a_never_started_run_agrees_as_both_absent(self):
        self.assertIsNone(workflow_contradiction_report(self.root, "run-nothing"))

    def test_an_unsafe_run_id_is_refused_rather_than_resolved(self):
        self.assertIsNone(workflow_contradiction_report(self.root, "../escape"))


class RealDivergenceIsReportedTests(_WorkflowFixture):
    """The comparator has to catch a divergence, not merely tolerate agreement."""

    def _rewrite_snapshot(self, run_id: str, mutate) -> dict:
        path = workflow_log_path(self.root, run_id)
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        mutate(snapshot)
        path.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8"
        )
        return snapshot

    def test_an_out_of_band_run_state_change_is_reported(self):
        """The scenario #613 exists for: a run's outcome rewritten in place."""

        self._run()
        original = read_workflow_log(self.root, "run-a")["run"]["run_state"]
        self._rewrite_snapshot(
            "run-a", lambda s: s.__setitem__("run_state", "cancelled")
        )

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertIn("run_state", report["mismatched_fields"])
        self.assertEqual(report["journal"]["run_state"], original)
        self.assertEqual(report["run_id"], "run-a")

    def test_a_truncated_state_history_is_reported(self):
        """Losing the middle of a run's history is exactly what replay recovers."""

        self._run()
        self._rewrite_snapshot(
            "run-a", lambda s: s.__setitem__("state_history", s["state_history"][:1])
        )

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertIn("state_history", report["mismatched_fields"])

    def test_a_step_level_divergence_is_reported_with_its_index(self):
        """Step state is journalled too, so a step-only edit must not slip past."""

        self._run()

        def break_step(snapshot):
            snapshot["steps"][0]["run_state"] = "failed"

        self._rewrite_snapshot("run-a", break_step)

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertIn("steps[0].run_state", report["mismatched_fields"])

    def test_a_lost_snapshot_is_reported_against_a_surviving_journal(self):
        self._run()
        workflow_log_path(self.root, "run-a").unlink()

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertIn("snapshot_missing", report["mismatched_fields"])
        self.assertEqual(report["snapshot"], {})
        self.assertIsNotNone(report["journal"]["run_state"])

    def test_a_lost_journal_is_reported_with_its_own_marker(self):
        """Distinguished from a state mismatch on purpose.

        A run migrated from the pre-journal schema has no journal to disagree
        with. If that reported as a generic mismatch, a backlog of legacy runs
        would bury real contradictions in noise.
        """

        self._run()
        workflow_journal_path(self.root, "run-a").unlink()

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertEqual(report["mismatched_fields"], ["journal_absent"])

    def test_a_migrated_legacy_run_is_marked_as_legacy_not_as_a_mismatch(self):
        self._run()
        workflow_journal_path(self.root, "run-a").unlink()
        self._rewrite_snapshot(
            "run-a", lambda s: s.__setitem__("migrated_from_schema_version", 1)
        )

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertEqual(report["mismatched_fields"], ["journal_absent_legacy_run"])


class TheComparatorMustNotRaiseWhereReadersDoTests(_WorkflowFixture):
    """A corrupt snapshot is the finding, not a crash."""

    def test_a_corrupt_snapshot_is_reported_rather_than_raising(self):
        self._run()
        workflow_log_path(self.root, "run-a").write_text("{not json", encoding="utf-8")

        report = workflow_contradiction_report(self.root, "run-a")

        self.assertIsNotNone(report)
        self.assertIn("snapshot_missing", report["mismatched_fields"])

    def test_a_corrupt_journal_line_does_not_crash_the_comparison(self):
        self._run()
        journal = workflow_journal_path(self.root, "run-a")
        journal.write_text(
            journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
        )

        # Whatever it decides, it must decide rather than raise.
        workflow_contradiction_report(self.root, "run-a")

    def test_the_comparison_never_writes_to_either_side(self):
        """A read-only report: running it must not change what it inspects."""

        self._run()
        snapshot_before = workflow_log_path(self.root, "run-a").read_bytes()
        journal_before = workflow_journal_path(self.root, "run-a").read_bytes()

        workflow_contradiction_report(self.root, "run-a")

        self.assertEqual(
            workflow_log_path(self.root, "run-a").read_bytes(), snapshot_before
        )
        self.assertEqual(
            workflow_journal_path(self.root, "run-a").read_bytes(), journal_before
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
