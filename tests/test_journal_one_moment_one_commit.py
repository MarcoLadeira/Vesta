"""One lifecycle moment, one transaction.

``journal_runtime``'s docstring promised that admission "inserts the task, the
run and the queued event together, or inserts none of them". It did not: the
rows, the lease and the event were three commits, and a terminal verdict, its
event and the lease release were three more. A failure between them left the
`runs` table and the event log telling different stories -- the contradiction
``journal_projections.run_table_parity`` exists to catch, created by the store
itself.

``journal_store._transaction`` is re-entrant now, so each moment commits once.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_runtime, journal_store

NOW = "2026-09-10T10:00:00+00:00"
LATER = "2026-09-10T10:01:00+00:00"


def _failing_on(event_type: str):
    real = journal_runtime.append_event

    def append(store, **kwargs):
        if kwargs.get("event_type") == event_type:
            raise sqlite3.OperationalError("disk I/O error")
        return real(store, **kwargs)

    return append


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def count(self, table: str) -> int:
        store = journal_store.open_store(self.root)
        try:
            return int(store.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # nosec B608 - fixed table names
        finally:
            store.close()

    def admit(self) -> int | None:
        return journal_runtime.record_admission(
            self.root, task_id="task-1", run_id="run-1", task="a turn", now=NOW
        )


class TransactionsCanBeJoinedTests(_Root):
    def test_an_inner_block_joins_the_outer_transaction(self):
        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        with journal_store._transaction(store):
            store.execute("INSERT INTO schema_meta(key, value) VALUES ('probe', '1')")
            with journal_store._transaction(store):
                store.execute("UPDATE schema_meta SET value = '2' WHERE key = 'probe'")
            # The inner block did not commit on its own.
            self.assertTrue(store.in_transaction)
        value = store.execute(
            "SELECT value FROM schema_meta WHERE key = 'probe'"
        ).fetchone()[0]
        self.assertEqual(value, "2")

    def test_a_failure_inside_rolls_back_the_whole_moment(self):
        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        with self.assertRaises(RuntimeError):
            with journal_store._transaction(store):
                store.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('probe', '1')"
                )
                with journal_store._transaction(store):
                    raise RuntimeError("the inner half failed")
        self.assertIsNone(
            store.execute(
                "SELECT value FROM schema_meta WHERE key = 'probe'"
            ).fetchone()
        )
        self.assertFalse(store.in_transaction)


class AdmissionIsOneCommitTests(_Root):
    def test_an_admission_that_fails_halfway_leaves_nothing(self):
        with mock.patch.object(
            journal_runtime,
            "append_event",
            side_effect=_failing_on(journal_runtime.EVENT_ADMITTED),
        ):
            self.assertIsNone(self.admit())

        # Before: the task and run rows and the lease had each committed, so a
        # run existed with no admitted event and a lease nobody would release.
        self.assertEqual(self.count("runs"), 0)
        self.assertEqual(self.count("tasks"), 0)
        self.assertEqual(self.count("leases"), 0)

    def test_a_healthy_admission_still_writes_all_of_it(self):
        self.assertIsNotNone(self.admit())

        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("leases"), 1)
        self.assertEqual(self.count("events"), 1)


class TerminalIsOneCommitTests(_Root):
    def finish(self, fence: int | None) -> bool:
        return journal_runtime.record_terminal(
            self.root,
            run_id="run-1",
            event_type=journal_runtime.EVENT_FINISHED,
            verdict="completed",
            reason="answered",
            now=LATER,
            fence=fence,
        )

    def row(self) -> dict:
        store = journal_store.open_store(self.root)
        try:
            run = store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = 'run-1'"
            ).fetchone()
            lease = store.execute(
                "SELECT released_at FROM leases WHERE run_id = 'run-1'"
            ).fetchone()
            return {"verdict": run[0], "released_at": lease[0]}
        finally:
            store.close()

    def test_a_terminal_write_that_fails_halfway_leaves_the_run_open(self):
        fence = self.admit()

        with mock.patch.object(
            journal_runtime,
            "append_event",
            side_effect=_failing_on(journal_runtime.EVENT_FINISHED),
        ):
            self.assertFalse(self.finish(fence))

        # Before: the verdict had committed with no event behind it, and the
        # lease stayed held -- "finished" in one table, running in the other.
        row = self.row()
        self.assertFalse(row["verdict"], "a verdict committed with no event")
        self.assertIsNone(row["released_at"])

    def test_the_run_can_still_be_finished_afterwards(self):
        fence = self.admit()
        with mock.patch.object(
            journal_runtime,
            "append_event",
            side_effect=_failing_on(journal_runtime.EVENT_FINISHED),
        ):
            self.finish(fence)

        self.assertTrue(self.finish(fence))
        self.assertEqual(self.row()["verdict"], "completed")
        self.assertIsNotNone(self.row()["released_at"])


if __name__ == "__main__":
    unittest.main()
