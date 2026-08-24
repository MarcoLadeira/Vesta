"""#613 Stage 3: journal-backed run lifecycle, written but not yet read.

Stage 3 asks for admission, execution, cancellation and verification to be
journal-backed. This is the adapter that does the writing, and the property
that matters most is what it does when it *fails*.

``gui_pipeline`` wraps its admission gate in ``contextlib.suppress`` with the
comment "never block a turn". That judgement does not change because the writer
is new: nothing reads from the journal yet (Stage 5 is the cutover), so a
missing mirror costs evidence, not correctness -- while a raised exception
would cost the user their run. Every entry point returns ``None`` or ``False``
instead of raising, and the tests below try to break each one.

The second theme is fencing. Being fenced out is the system working, not an
error, so a stale writer's call returns ``None`` and writes nothing rather than
surfacing to a turn that can do nothing useful about it.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_runtime
from opaihub.journal_runtime import (
    EVENT_ADMITTED,
    EVENT_CANCELLED,
    EVENT_FINISHED,
    EVENT_STARTED,
    record_admission,
    record_event,
    record_terminal,
)
from opaihub.journal_store import open_store, read_events

NOW = "2026-08-23T12:00:00+00:00"


class _RuntimeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _admit(self, *, task_id: str = "task-a", run_id: str = "run-a", **kw):
        return record_admission(
            self.root,
            task_id=task_id,
            run_id=run_id,
            task="fix the failing login test",
            now=NOW,
            **kw,
        )

    def _store(self):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store


class AdmissionIsOneTransactionTests(_RuntimeFixture):
    def test_admission_records_task_run_and_event_together(self):
        fence = self._admit()

        self.assertEqual(fence, 1)
        store = self._store()
        self.assertEqual(store.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(store.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(
            [row["event_type"] for row in read_events(store)], [EVENT_ADMITTED]
        )

    def test_a_retry_is_a_second_run_of_the_same_task(self):
        """Failing on an existing task would leave every retry unjournalled."""

        self._admit(run_id="run-a")
        self._admit(run_id="run-b", attempt=2)

        store = self._store()
        self.assertEqual(store.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(store.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_readmitting_the_same_run_does_not_duplicate_it(self):
        self._admit()
        second = self._admit()

        store = self._store()
        self.assertEqual(store.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(second, 2, "a re-admission takes the lease over")

    def test_the_prompt_is_summarised_not_stored_whole(self):
        """#613's non-goals rule out keeping raw prompts to enable replay."""

        long_task = "x" * 5000
        record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task=long_task,
            now=NOW,
        )

        stored = (
            self._store().execute("SELECT requested_outcome FROM tasks").fetchone()[0]
        )

        self.assertLessEqual(len(stored), 200)

    def test_a_multiline_prompt_becomes_one_line(self):
        record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task="fix   the\n\n  login\ttest",
            now=NOW,
        )

        stored = (
            self._store().execute("SELECT requested_outcome FROM tasks").fetchone()[0]
        )

        self.assertEqual(stored, "fix the login test")


class LifecycleEventsTests(_RuntimeFixture):
    def test_the_run_lifecycle_lands_in_order(self):
        fence = self._admit()
        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            now=NOW,
            fence=fence,
        )
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="ok",
            now=NOW,
            fence=fence,
        )

        store = self._store()
        self.assertEqual(
            [row["event_type"] for row in read_events(store)],
            [EVENT_ADMITTED, EVENT_STARTED, EVENT_FINISHED],
        )

    def test_a_terminal_verdict_lands_on_the_run_row_too(self):
        """A reader of either the row or the events must see the same ending."""

        fence = self._admit()
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_CANCELLED,
            verdict="cancelled",
            reason="user_requested",
            now=NOW,
            fence=fence,
        )

        row = (
            self._store()
            .execute(
                "SELECT observed_state, terminal_verdict, terminal_reason FROM runs"
            )
            .fetchone()
        )

        self.assertEqual(row["terminal_verdict"], "cancelled")
        self.assertEqual(row["terminal_reason"], "user_requested")

    def test_a_terminal_call_releases_the_lease(self):
        """A finished run must not hold a lease nobody will release."""

        fence = self._admit()
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="ok",
            now=NOW,
            fence=fence,
        )

        released = (
            self._store()
            .execute("SELECT released_at FROM leases WHERE run_id = 'run-a'")
            .fetchone()[0]
        )

        self.assertIsNotNone(released)


class BeingFencedOutIsNotAnErrorTests(_RuntimeFixture):
    """A stale writer is refused quietly; the turn can do nothing about it."""

    def test_a_stale_fence_writes_nothing_and_does_not_raise(self):
        stale = self._admit()
        self._admit()  # takeover bumps the fence

        result = record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            now=NOW,
            fence=stale,
        )

        self.assertIsNone(result)
        types = [row["event_type"] for row in read_events(self._store())]
        self.assertNotIn(EVENT_STARTED, types)

    def test_a_stale_terminal_call_does_not_overwrite_the_verdict(self):
        """The dangerous one: a zombie must not settle a run it lost."""

        stale = self._admit()
        current = self._admit()
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="ok",
            now=NOW,
            fence=current,
        )

        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_CANCELLED,
            verdict="cancelled",
            reason="zombie",
            now=NOW,
            fence=stale,
        )

        verdict = (
            self._store().execute("SELECT terminal_verdict FROM runs").fetchone()[0]
        )

        self.assertEqual(verdict, "completed")


class AJournalFailureNeverFailsATurnTests(_RuntimeFixture):
    """The rule gui_pipeline already states for admission, kept for the mirror."""

    def test_admission_returns_none_when_the_store_cannot_be_opened(self):
        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("read-only")
        ):
            self.assertIsNone(self._admit())

    def test_admission_returns_none_when_the_write_fails(self):
        with mock.patch.object(
            journal_runtime,
            "acquire_lease",
            side_effect=sqlite3.DatabaseError("locked"),
        ):
            self.assertIsNone(self._admit())

    def test_an_event_returns_none_when_the_store_cannot_be_opened(self):
        self._admit()

        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("read-only")
        ):
            result = record_event(
                self.root, run_id="run-a", event_type=EVENT_STARTED, now=NOW
            )

        self.assertIsNone(result)

    def test_a_terminal_call_returns_false_rather_than_raising(self):
        self._admit()

        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("read-only")
        ):
            result = record_terminal(
                self.root,
                run_id="run-a",
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
            )

        self.assertFalse(result)

    def test_a_failed_admission_leaves_no_half_written_run(self):
        """Partial state is the contradiction #613 opens by describing."""

        with mock.patch.object(
            journal_runtime, "append_event", side_effect=sqlite3.DatabaseError("boom")
        ):
            self.assertIsNone(self._admit())

        store = self._store()
        events = read_events(store)
        self.assertEqual(events, [], "no event, so no run may claim to have one")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
