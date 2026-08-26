"""#613 Stage 7: legacy writes retire only when the telemetry says so.

Stage 7's instruction is one sentence and every word is a precondition:

    Remove authoritative legacy writes only after usage telemetry is zero and
    migration/recovery tests pass.

So almost every test here is about **refusing**. That imbalance is the point:
a false "not ready" costs another week of dual writes, while a false "ready"
deletes the only remaining copy of state that turns out to have been needed,
and there is no undo. The tests are weighted the way the risk is.

The single "ready" test exists to keep the gate honest in the other direction.
A check that can never pass is not a gate, it is a wall -- and a wall would
guarantee this migration never finishes, which is its own kind of failure.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import idempotency, journal_retirement
from opaihub.journal_retirement import (
    BLOCK_INTEGRITY,
    BLOCK_LEGACY_READS,
    BLOCK_NO_JOURNAL,
    BLOCK_SAMPLE,
    BLOCK_UNQUALIFIED,
    BLOCK_UNRECONCILED,
    STATUS_BLOCKED,
    STATUS_READY,
    assess,
    legacy_writes_required,
)
from opaihub.journal_runtime import EVENT_FINISHED, record_admission, record_terminal
from opaihub.journal_store import journal_path

NOW = "2026-08-25T12:00:00+00:00"


class _RetirementFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _migrated(self, count: int) -> dict[str, dict[str, str]]:
        """``count`` runs that exist in both records and agree."""

        legacy = {}
        for index in range(count):
            run_id = f"run-{index}"
            fence = record_admission(
                self.root,
                task_id="task-a",
                run_id=run_id,
                task="a task",
                now=NOW,
            )
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
                fence=fence,
            )
            legacy[run_id] = {
                "terminal_verdict": "completed",
                "created_at": "2099-01-01T00:00:00+00:00",
            }
        return legacy


class EveryUncertaintyBlocksTests(_RetirementFixture):
    def test_a_project_with_no_journal_cannot_retire(self):
        report = assess(self.root, {})

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_NO_JOURNAL, report.blockers)

    def test_too_few_runs_blocks_even_when_everything_agrees(self):
        """One agreeing run proves the plumbing connects, not that it holds."""

        legacy = self._migrated(3)

        report = assess(self.root, legacy, minimum_runs=20)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_SAMPLE, report.blockers)

    def test_a_single_legacy_read_blocks(self):
        """The literal telemetry the instruction names: zero, not nearly zero."""

        legacy = self._migrated(25)
        legacy["ancient"] = {
            "terminal_verdict": "completed",
            "created_at": "1999-01-01T00:00:00+00:00",
        }

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_LEGACY_READS, report.blockers)
        self.assertEqual(report.legacy_reads, 1)

    def test_an_unqualified_journal_blocks(self):
        legacy = self._migrated(25)
        legacy["run-0"] = {
            "terminal_verdict": "cancelled",  # disagrees with the journal
            "created_at": "2099-01-01T00:00:00+00:00",
        }

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_UNQUALIFIED, report.blockers)

    def test_a_degraded_journal_blocks_even_though_it_is_readable(self):
        """Degraded is good enough to read from and not to remove a fallback.

        The whole purpose of keeping the legacy record is having something left
        when the journal is imperfect, so "imperfect but usable" is exactly the
        state in which it must be kept.
        """

        legacy = self._migrated(25)
        from opaihub.journal_store import append_event, open_store

        store = open_store(self.root)
        try:
            bad = append_event(
                store,
                event_type="noise",
                payload={"x": 1},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-0",
            )
            store.execute(
                "UPDATE events SET payload = '{torn' WHERE sequence = ?", (bad,)
            )
        finally:
            store.close()

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_INTEGRITY, report.blockers)

    def test_a_corrupt_journal_blocks(self):
        legacy = self._migrated(25)
        journal_path(self.root).write_bytes(b"not a database")

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_INTEGRITY, report.blockers)

    def test_an_unreconciled_operation_blocks(self):
        """An effect whose outcome is unknown is what legacy might still explain."""

        legacy = self._migrated(25)
        idempotency.begin(
            self.root, idempotency.operation_key("github.pr", head="feat/x")
        )

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertIn(BLOCK_UNRECONCILED, report.blockers)
        self.assertEqual(report.unreconciled, 1)

    def test_a_reconciled_operation_does_not_block(self):
        """Teeth the other way: finished work must not hold the gate shut."""

        legacy = self._migrated(25)
        key = idempotency.operation_key("github.pr", head="feat/x")
        idempotency.begin(self.root, key)
        idempotency.complete(self.root, key, {"id": 1})

        report = assess(self.root, legacy, minimum_runs=5)

        self.assertNotIn(BLOCK_UNRECONCILED, report.blockers)


class EveryBlockerIsNamedAtOnceTests(_RetirementFixture):
    """Fix one, re-run, discover another is a bad way to run a migration."""

    def test_several_blockers_are_reported_together(self):
        legacy = self._migrated(2)
        legacy["ancient"] = {
            "terminal_verdict": "completed",
            "created_at": "1999-01-01T00:00:00+00:00",
        }
        idempotency.begin(self.root, idempotency.operation_key("git.push", b="x"))

        report = assess(self.root, legacy, minimum_runs=20)

        self.assertGreaterEqual(len(report.blockers), 3)
        for blocker in (BLOCK_SAMPLE, BLOCK_LEGACY_READS, BLOCK_UNRECONCILED):
            self.assertIn(blocker, report.blockers)

    def test_the_detail_explains_each_blocker_in_words(self):
        report = assess(self.root, {})

        self.assertIn("nothing to retire onto", report.detail)

    def test_the_report_is_json_serialisable(self):
        legacy = self._migrated(2)

        payload = assess(self.root, legacy, minimum_runs=20).to_dict()

        json.loads(json.dumps(payload))
        self.assertEqual(payload["report"], "opai-journal-retirement")
        self.assertFalse(payload["ready"])


class TheGateCanActuallyOpenTests(_RetirementFixture):
    """A check that can never pass is a wall, and a wall never finishes either."""

    def test_a_fully_migrated_project_is_ready(self):
        legacy = self._migrated(25)

        report = assess(self.root, legacy, minimum_runs=20)

        self.assertEqual(report.status, STATUS_READY, report.detail)
        self.assertTrue(report.ready)
        self.assertEqual(report.legacy_reads, 0)
        self.assertEqual(report.unreconciled, 0)
        self.assertEqual(report.journal_reads, 25)


class LegacyWritesRequiredTests(_RetirementFixture):
    """The one call a write site makes, biased to write on any doubt."""

    def test_writes_are_required_while_anything_is_unresolved(self):
        self.assertTrue(legacy_writes_required(self.root, {}))

    def test_writes_are_not_required_once_the_gate_opens(self):
        legacy = self._migrated(25)

        self.assertFalse(legacy_writes_required(self.root, legacy, minimum_runs=20))

    def test_a_failed_check_still_requires_the_write(self):
        """The bias that matters: a broken check must never skip a write.

        A needless legacy write costs a duplicated file. A wrongly-skipped one
        costs state that then exists nowhere.
        """

        with mock.patch.object(
            journal_retirement, "assess", side_effect=RuntimeError("boom")
        ):
            self.assertTrue(legacy_writes_required(self.root, {}))

    def test_the_answer_is_not_cached_across_a_change(self):
        """A cached "no" that outlives its justification loses data quietly."""

        legacy = self._migrated(25)
        self.assertFalse(legacy_writes_required(self.root, legacy, minimum_runs=20))

        # Something regresses -- an operation is claimed and not confirmed.
        idempotency.begin(self.root, idempotency.operation_key("git.push", b="x"))

        self.assertTrue(
            legacy_writes_required(self.root, legacy, minimum_runs=20),
            "the gate must re-close when the conditions that opened it change",
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
