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

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo

from opaihub import gui_pipeline, journal_runtime, journal_store

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
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_repo(Path(self._tmp.name), files={"a.py": "x = 1\n"})

    def test_a_turn_beats_its_own_run_even_with_a_long_interval(self):
        """With an hour-long interval a turn gets exactly one beat.

        Before, that one beat was spent on an event that fired before
        admission, when there was no run to beat -- so the turn got none.
        """

        beats: list[str] = []

        def recording_beat(root, *, run_id, now):
            beats.append(run_id)
            return True

        with (
            mock.patch.object(gui_pipeline, "_HEARTBEAT_INTERVAL_SECONDS", 3600),
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
        self.assertEqual(beats, reported, "the turn's own run was never beaten")


if __name__ == "__main__":
    unittest.main()
