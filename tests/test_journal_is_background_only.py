"""#818: the runtime journal is bookkeeping, and bookkeeping never stops work.

Vesta is a coding tool. Everything in this epic -- admission, leases, terminal
verdicts, cost provenance, cancellation phases -- exists so Vesta can say true
things about what it did. None of it is worth one message a user could not
send, and somebody programming should not be able to tell it is there.

So these tests are about *absence of effect*, and they are deliberately brutal
about it: the journal is broken completely, on every entry point at once, and
a turn still has to finish and return its answer unchanged.

The cost tests are written as a **ratio**, not a stopwatch. Measuring
milliseconds makes the answer a property of whichever disk the temp directory
landed on, and that is not a hypothetical: the first version of this file
reported 686 ms per turn and concluded the journal was ruining the experience.
It was measuring a scratch drive where `sqlite3` close costs 215 ms against
the 2.8 ms it costs on the drive Vesta actually lives on. The real figure is
about 24 ms per turn -- under one percent of a turn that calls a model.

A ratio against a plain commit on the *same* filesystem is immune to that.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import vestahub.gui_pipeline as gui_pipeline
from vestahub import journal_runtime

NOW = "2026-09-10T10:00:00+00:00"

#: Every entry point a live turn can reach. Broken all at once, because a turn
#: does not fail one call at a time.
ENTRY_POINTS = (
    "record_admission",
    "record_event",
    "record_terminal",
    "record_run_snapshot",
    "record_run_cost",
    "record_verification",
    "beat_lease",
    "record_cancellation_phase",
    "unterminated_runs",
    "unterminated_summary",
)


class _Sabotaged:
    """A journal where nothing works, in the loudest way available."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._patches: list[Any] = []  # type: ignore[name-defined]

    def __enter__(self) -> "_Sabotaged":
        self._patches = []
        for name in ENTRY_POINTS:
            if not hasattr(journal_runtime, name):
                continue

            def explode(*_args, _name: str = name, **_kwargs):
                self.calls.append(_name)
                raise RuntimeError(f"the journal is on fire: {_name}")

            patch = mock.patch.object(journal_runtime, name, explode)
            patch.start()
            self._patches.append(patch)
        return self

    def __exit__(self, *exc: object) -> bool:
        for patch in self._patches:
            patch.stop()
        return False


class ATotallyBrokenJournalDoesNotStopATurnTests(unittest.TestCase):
    """Every entry point raising, and the answer still arrives."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_turn(self, result: dict) -> dict:
        """One turn through the real wrapper, with only the work stubbed.

        The stub publishes the run identity before returning, because that is
        what the real implementation does during admission -- and the wrapper
        clears the ContextVar on entry, so setting it *outside* achieves
        nothing. Getting that wrong is how the first version of this file
        passed while never reaching the journal at all, which
        `test_the_journal_really_was_reached` now refuses to allow.
        """

        def stubbed_turn(*_args, **_kwargs):
            gui_pipeline._JOURNAL_RUN.set({"run_id": "run-1", "fence": 1})
            return result

        with mock.patch.object(gui_pipeline, "_handle_gui_message", stubbed_turn):
            return gui_pipeline.handle_gui_message(self.root, "do the thing")

    def test_the_journal_really_was_reached(self):
        """Otherwise every test below proves only that nothing happened."""

        sabotage = _Sabotaged()
        with sabotage:
            self.run_turn({"status": "answered", "answer": "done"})

        self.assertTrue(
            sabotage.calls,
            "the journal was never called, so surviving it proves nothing",
        )

    def test_the_answer_still_arrives(self):
        with _Sabotaged():
            got = self.run_turn({"status": "answered", "answer": "here is your code"})

        self.assertEqual(got["answer"], "here is your code")
        self.assertEqual(got["status"], "answered")

    def test_the_result_is_not_modified_at_all(self):
        """Not "mostly unchanged". Byte for byte what the turn produced."""

        answer = {
            "status": "answered",
            "answer": "here is your code",
            "events": [{"kind": "tool", "detail": "edited a file"}],
            "completion_verdict": {"verdict": "completed"},
        }
        expected = json.dumps(answer, sort_keys=True)

        with _Sabotaged():
            got = self.run_turn(dict(answer))

        self.assertEqual(json.dumps(got, sort_keys=True), expected)

    def test_no_journal_failure_appears_in_what_the_user_sees(self):
        with _Sabotaged():
            got = self.run_turn({"status": "answered", "answer": "done"})

        rendered = repr(got).lower()
        for leak in ("journal", "on fire", "sqlite", "runtimeerror", "traceback"):
            self.assertNotIn(leak, rendered, f"{leak!r} reached the user")

    def test_a_failing_turn_reports_its_own_failure_not_the_journals(self):
        """The turn's error must survive; the journal's must not replace it."""

        def boom(*_a, **_k):
            gui_pipeline._JOURNAL_RUN.set({"run_id": "run-1", "fence": 1})
            raise ValueError("the model refused")

        with _Sabotaged():
            with mock.patch.object(gui_pipeline, "_handle_gui_message", boom):
                with self.assertRaises(ValueError) as caught:
                    gui_pipeline.handle_gui_message(self.root, "do it")

        self.assertIn("the model refused", str(caught.exception))

    def test_a_cancelled_turn_survives_it_too(self):
        with _Sabotaged():
            got = self.run_turn({"status": "cancelled", "answer": ""})

        self.assertEqual(got["status"], "cancelled")


class TheJournalStaysCheapTests(unittest.TestCase):
    """Succeeding slowly is also affecting the experience.

    Every bound here is a *ratio* against a plain SQLite commit on the same
    filesystem, so a slow disk moves both numbers and the test still means the
    same thing. Absolute milliseconds would make this a disk benchmark.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _baseline_commit_seconds(self, samples: int = 40) -> float:
        """What one durable SQLite commit costs on this filesystem, right now."""

        path = self.root / "baseline.sqlite3"
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE t (a INTEGER)")
            started = time.monotonic()
            for index in range(samples):
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO t VALUES (?)", (index,))
                connection.execute("COMMIT")
            return (time.monotonic() - started) / samples
        finally:
            connection.close()

    def test_a_whole_turns_bookkeeping_costs_a_handful_of_commits(self):
        """Admission, a heartbeat and an ending together.

        The budget is generous because the point is to catch a change that
        makes journalling cost *orders* more, not to police a few percent.
        """

        baseline = self._baseline_commit_seconds()

        started = time.monotonic()
        turns = 15
        for index in range(turns):
            run_id = f"run-{index}"
            fence = journal_runtime.record_admission(
                self.root,
                task_id=f"task-{index}",
                run_id=run_id,
                task="a turn",
                now=NOW,
                surface="gui",
            )
            journal_runtime.beat_lease(self.root, run_id=run_id, now=NOW)
            journal_runtime.record_terminal(
                self.root,
                run_id=run_id,
                event_type=journal_runtime.EVENT_FINISHED,
                verdict="completed",
                reason="answered",
                now=NOW,
                fence=fence,
            )
        per_turn = (time.monotonic() - started) / turns

        ratio = per_turn / baseline if baseline > 0 else float("inf")
        # Measured 28.2 on the drive Vesta lives on and 19.3 on a much slower
        # scratch drive -- the ratio is stable precisely because both numbers
        # move together, which is why it is expressed this way. 50 leaves
        # under a factor of two of headroom: loose enough not to flake on a
        # busy machine, tight enough that doubling the commits per turn fails.
        self.assertLess(
            ratio,
            50.0,
            f"one turn's bookkeeping costs {ratio:.1f} durable commits "
            f"({per_turn * 1000:.1f} ms against a {baseline * 1000:.3f} ms "
            "baseline). Around 20-30 is the shape of this work; a big jump "
            "means somebody added writes to the critical path of every turn.",
        )

    def test_a_heartbeat_is_cheaper_than_admission(self):
        """It fires as phases advance, so it is the hottest path here."""

        fence = journal_runtime.record_admission(
            self.root,
            task_id="task-1",
            run_id="run-1",
            task="a turn",
            now=NOW,
            surface="gui",
        )
        self.assertIsNotNone(fence)

        started = time.monotonic()
        for _ in range(30):
            journal_runtime.beat_lease(self.root, run_id="run-1", now=NOW)
        per_beat = (time.monotonic() - started) / 30

        started = time.monotonic()
        for index in range(10):
            journal_runtime.record_admission(
                self.root,
                task_id=f"task-b{index}",
                run_id=f"run-b{index}",
                task="a turn",
                now=NOW,
                surface="gui",
            )
        per_admission = (time.monotonic() - started) / 10

        self.assertLess(
            per_beat,
            per_admission,
            "the heartbeat fires far more often than admission, so it must "
            "not be the more expensive of the two",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
