"""#818 review findings 8 and 20: the heartbeat, on the turn's own thread.

8.  ``beat_lease`` opened the store with the ordinary ten-second busy timeout,
    on the thread streaming the answer. Any process holding the write lock --
    a backup, a compaction, a migration -- froze the answer for about eleven
    seconds. A heartbeat now skips rather than waits.
20. The throttle was spent by the first event of a turn, which fires before
    admission and so has no run to beat. The first real beat came a whole
    interval late.
"""

from __future__ import annotations

import inspect
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo

from vestahub import gui_pipeline, journal_runtime, journal_store

NOW = "2026-09-10T10:00:00+00:00"
LATER = "2026-09-10T10:00:30+00:00"


class ABeatNeverWaitsForTheLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        journal_runtime.record_admission(
            self.root, task_id="task-1", run_id="run-1", task="t", now=NOW
        )

    def hold_the_write_lock(self) -> sqlite3.Connection:
        holder = sqlite3.connect(
            journal_store.journal_path(self.root), isolation_level=None
        )
        holder.execute("BEGIN IMMEDIATE")
        self.addCleanup(holder.close)
        return holder

    def test_a_held_lock_costs_a_beat_not_the_turn(self):
        holder = self.hold_the_write_lock()

        started = time.monotonic()
        beaten = journal_runtime.beat_lease(self.root, run_id="run-1", now=LATER)
        waited = time.monotonic() - started

        holder.execute("ROLLBACK")
        self.assertFalse(beaten, "the beat cannot have landed under someone's lock")
        # Before: ~11 s. The bound is generous for a slow disk and still far
        # below anything a person would see as the answer freezing.
        self.assertLess(waited, 2.0, f"the beat waited {waited:.1f}s for the lock")

    def test_the_next_beat_lands_once_the_lock_is_gone(self):
        holder = self.hold_the_write_lock()
        journal_runtime.beat_lease(self.root, run_id="run-1", now=LATER)
        holder.execute("ROLLBACK")

        self.assertTrue(
            journal_runtime.beat_lease(self.root, run_id="run-1", now=LATER)
        )

    def test_ordinary_writes_still_wait_for_their_turn(self):
        """Only the heartbeat is impatient; a verdict must still land."""

        self.assertEqual(
            journal_store.BUSY_TIMEOUT_SECONDS,
            10.0,
            "the default busy timeout is for writes that must not be lost",
        )


class TheFirstBeatIsOnTimeTests(unittest.TestCase):
    """The throttle decision itself, which the first version got wrong.

    A turn's first events fire before admission, when there is no run to beat.
    Spending the throttle on one of them left the first real beat a whole
    heartbeat interval late.

    Tested through the decision rather than through the clock: `monotonic()` is
    time since boot, so an earlier version of this test quietly depended on the
    machine having been up longer than the interval it patched in -- it passed
    all day and failed after a restart.
    """

    def due(self, last: float, now: float, *, has_run: bool) -> bool:
        return gui_pipeline._due_for_a_beat(last, now, has_run=has_run)

    def test_an_event_before_admission_does_not_spend_the_throttle(self):
        interval = gui_pipeline._HEARTBEAT_INTERVAL_SECONDS

        self.assertFalse(self.due(0.0, interval + 1, has_run=False))

    def test_the_first_event_after_admission_beats(self):
        interval = gui_pipeline._HEARTBEAT_INTERVAL_SECONDS

        self.assertTrue(self.due(0.0, interval + 1, has_run=True))

    def test_a_beat_just_taken_is_not_repeated(self):
        interval = gui_pipeline._HEARTBEAT_INTERVAL_SECONDS

        self.assertFalse(self.due(1000.0, 1000.0 + interval / 2, has_run=True))
        self.assertTrue(self.due(1000.0, 1000.0 + interval, has_run=True))

    def test_the_moment_judged_due_is_the_moment_recorded(self):
        """The emitter read the clock twice: once to judge, once to stamp."""

        source = inspect.getsource(gui_pipeline._handle_gui_message)

        self.assertIn("beat_at = time.monotonic()", source)
        self.assertIn("_last_beat[0] = beat_at", source)


class ATurnReallyBeatsItsOwnRunTests(unittest.TestCase):
    """The wiring, so the decision above is not being tested in a vacuum."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_repo(Path(self._tmp.name), files={"a.py": "x = 1" + chr(10)})

    def test_the_beats_a_turn_takes_name_that_turn(self):
        beats: list[str] = []

        def recording_beat(root, *, run_id, now):
            beats.append(run_id)
            return True

        # Every event is due, so the beats are observable without waiting.
        with (
            mock.patch.object(gui_pipeline, "_HEARTBEAT_INTERVAL_SECONDS", 0),
            mock.patch.object(journal_runtime, "beat_lease", recording_beat),
        ):
            reported: list[str] = []
            gui_pipeline.handle_gui_message(
                self.root,
                "explain what a.py does",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="It sets x."),
                on_journal_run=reported.append,
            )

        self.assertEqual(len(reported), 1, "the turn was never admitted")
        self.assertTrue(beats, "a turn took no heartbeat at all")
        self.assertEqual(set(beats), set(reported), "a beat named another run")


if __name__ == "__main__":
    unittest.main()
