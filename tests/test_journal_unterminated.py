"""#613: which runs never finished, which is the first question after a crash.

The issue opens by describing this state and names it as the thing recovery
cannot currently reason about:

    A run may appear active with no worker or disappear after restart.
    ...
    Recovery logic cannot know whether to resume, reconcile, block or request
    attention.

``journal_operations.unreconciled_operations`` already answered that for
external effects. Runs had no equivalent, so the half of the question that
concerns *work* rather than *side effects* could not be asked at all.

The design decision worth defending is that this **reports and does not
conclude**. An unterminated run whose lease is still held is either running
right now or was abandoned by a process that died, and nothing in the database
distinguishes them: a lease is released by ``record_terminal``, not by a
process exiting. So each row carries the owner and the heartbeat, and the
caller -- which can look at whether that process still exists -- decides.

Inventing the distinction here would be a plausible answer with nothing behind
it, which is the failure mode this whole issue exists to remove.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed argv, throwaway project
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_runtime
from opaihub.journal_runtime import (
    EVENT_FINISHED,
    record_admission,
    record_terminal,
    unterminated_runs,
    unterminated_summary,
)
from opaihub.journal_store import journal_path, open_store

NOW = "2026-08-27T12:00:00+00:00"


class _PendingFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _admit(self, run_id: str, *, finish: bool = False) -> int | None:
        fence = record_admission(
            self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW
        )
        if finish:
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
                fence=fence,
            )
        return fence

    def _ids(self) -> set[str]:
        return {entry["run_id"] for entry in unterminated_runs(self.root)}


class OnlyRunsWithoutAnEndingAreListedTests(_PendingFixture):
    def test_a_finished_run_is_not_pending(self):
        self._admit("done", finish=True)

        self.assertEqual(self._ids(), set())

    def test_an_admitted_run_with_no_ending_is_pending(self):
        self._admit("live")

        self.assertEqual(self._ids(), {"live"})

    def test_finished_and_unfinished_runs_are_separated(self):
        self._admit("done", finish=True)
        self._admit("live")
        self._admit("also-live")

        self.assertEqual(self._ids(), {"live", "also-live"})

    def test_every_terminal_verdict_counts_as_an_ending(self):
        """Cancelled and failed are endings too, not just completion."""

        for index, verdict in enumerate(("cancelled", "failed", "timeout")):
            run_id = f"run-{index}"
            fence = self._admit(run_id)
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict=verdict,
                reason="",
                now=NOW,
                fence=fence,
            )

        self.assertEqual(self._ids(), set())

    def test_a_project_with_no_journal_has_nothing_pending(self):
        self.assertEqual(unterminated_runs(self.root), [])
        self.assertFalse(unterminated_summary(self.root)["available"])


class TheRowCarriesWhatARecoveryPassNeedsTests(_PendingFixture):
    def test_the_entry_names_the_run_its_task_and_its_attempt(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertEqual(entry["run_id"], "live")
        self.assertEqual(entry["task_id"], "task-a")
        self.assertEqual(entry["attempt"], 1)
        self.assertTrue(entry["created_at"])

    def test_a_held_lease_is_reported_with_its_owner(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertTrue(entry["lease_held"])
        self.assertTrue(entry["lease_owner"])
        self.assertTrue(entry["lease_heartbeat_at"])

    def test_a_released_lease_is_not_reported_as_held(self):
        """A lease can be released without the run reaching a verdict."""

        fence = self._admit("live")
        store = open_store(self.root)
        try:
            from opaihub.journal_store import release_lease

            release_lease(store, run_id="live", fence=fence, now=NOW)
        finally:
            store.close()

        entry = unterminated_runs(self.root)[0]

        self.assertFalse(entry["lease_held"])

    def test_the_oldest_run_comes_first(self):
        """A recovery pass works from the longest-outstanding one."""

        record_admission(
            self.root,
            task_id="t",
            run_id="older",
            task="x",
            now="2020-01-01T00:00:00+00:00",
        )
        record_admission(
            self.root,
            task_id="t",
            run_id="newer",
            task="x",
            now="2030-01-01T00:00:00+00:00",
        )

        self.assertEqual(
            [entry["run_id"] for entry in unterminated_runs(self.root)],
            ["older", "newer"],
        )

    def test_the_limit_is_honoured(self):
        for index in range(10):
            self._admit(f"run-{index}")

        self.assertEqual(len(unterminated_runs(self.root, limit=3)), 3)


class ARunAbandonedByADeadProcessIsVisibleTests(_PendingFixture):
    """The case the issue actually describes, produced by a real death."""

    def test_a_run_admitted_by_a_process_that_died_is_listed(self):
        script = (
            "import sys; sys.path.insert(0, r'{cwd}')\n"
            "from opaihub.journal_runtime import record_admission\n"
            "record_admission(r'{root}', task_id='t', run_id='orphan',"
            " task='x', now='{now}')\n"
            "import os; os._exit(9)\n"
        ).format(cwd=os.getcwd(), root=self.root, now=NOW)

        subprocess.run(  # nosec B603 - fixed argv, throwaway project
            [sys.executable, "-c", script], capture_output=True, check=False
        )

        entries = unterminated_runs(self.root)
        self.assertEqual([entry["run_id"] for entry in entries], ["orphan"])
        self.assertTrue(
            entries[0]["lease_held"],
            "the dead process never released its lease, which is the point",
        )


class ItReportsRatherThanConcludesTests(_PendingFixture):
    """The distinction this module refuses to invent.

    A run that is genuinely running and one abandoned mid-flight are
    indistinguishable in this database, because a lease is released by a
    terminal record rather than by a process exiting. Both must therefore look
    identical here -- if they ever stop looking identical, something has
    started guessing.
    """

    def test_a_live_run_and_an_abandoned_one_are_indistinguishable(self):
        self._admit("live")
        script = (
            "import sys; sys.path.insert(0, r'{cwd}')\n"
            "from opaihub.journal_runtime import record_admission\n"
            "record_admission(r'{root}', task_id='task-a', run_id='orphan',"
            " task='x', now='{now}')\n"
            "import os; os._exit(9)\n"
        ).format(cwd=os.getcwd(), root=self.root, now=NOW)
        subprocess.run(  # nosec B603 - fixed argv, throwaway project
            [sys.executable, "-c", script], capture_output=True, check=False
        )

        rows = {entry["run_id"]: entry for entry in unterminated_runs(self.root)}

        self.assertEqual(set(rows), {"live", "orphan"})
        self.assertEqual(rows["live"]["lease_held"], rows["orphan"]["lease_held"])

    def test_no_entry_claims_a_run_is_dead(self):
        """No field here asserts something the database cannot know."""

        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        for forbidden in ("orphaned", "dead", "abandoned", "stale", "crashed"):
            self.assertNotIn(forbidden, entry)


class TheSummaryCountsWithoutJudgingTests(_PendingFixture):
    def test_it_counts_pending_runs_and_held_leases(self):
        self._admit("done", finish=True)
        self._admit("live")
        self._admit("also-live")

        summary = unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 2)
        self.assertEqual(summary["lease_held"], 2)

    def test_a_fully_settled_project_reports_zero(self):
        self._admit("done", finish=True)

        summary = unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 0)


class ReportingNeverBecomesTheProblemTests(_PendingFixture):
    """This is read after a crash, which is the worst time to raise."""

    def test_a_corrupt_journal_yields_an_empty_list_rather_than_an_error(self):
        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database")

        self.assertEqual(unterminated_runs(self.root), [])

    def test_a_failure_to_open_the_store_is_not_an_error(self):
        self._admit("live")

        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("gone")
        ):
            self.assertEqual(unterminated_runs(self.root), [])

    def test_the_summary_never_raises(self):
        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database")

        summary = unterminated_summary(self.root)

        self.assertEqual(summary["unterminated"], 0)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
