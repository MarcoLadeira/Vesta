"""Illegal transitions are rejected *and observable* (#295 alpha gate 7).

Gate 7: "Illegal transitions: 0 unhandled; every attempted violation is
rejected **and observable**." The machine refused bad edges from the start —
that is the "rejected" half — but it refused them *silently*. So the one class
of bug this model exists to catch, something walking a path the lifecycle
forbids, left no trace anywhere. A guard nobody can read is a guard nobody can
audit, and gate 7 asks for a number that can be proven zero.

The gate is a release criterion, not a metric to tolerate: a non-zero count is a
defect report.
"""

from __future__ import annotations

import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub.run_state import (
    TERMINAL_STATES,
    RunState,
    illegal_transitions,
    reset_illegal_transitions,
    transition,
)

_MESSAGE_STATE_JS = (
    Path(__file__).resolve().parents[1] / "opai" / "assets" / "web" / "message-state.js"
)


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_illegal_transitions()

    def tearDown(self) -> None:
        reset_illegal_transitions()

    def test_a_refused_transition_is_recorded(self) -> None:
        transition(RunState.COMPLETED, RunState.RUNNING, source="pipeline")
        record = illegal_transitions()
        self.assertEqual(record["count"], 1)
        self.assertEqual(record["recent"][0]["from"], "completed")
        self.assertEqual(record["recent"][0]["to"], "running")
        self.assertEqual(record["recent"][0]["source"], "pipeline")

    def test_the_previous_state_is_still_preserved(self) -> None:
        # Observability must not come at the cost of the rejection itself.
        result = transition(RunState.COMPLETED, RunState.RUNNING)
        self.assertIs(result, RunState.COMPLETED)

    def test_a_legal_transition_records_nothing(self) -> None:
        transition(RunState.RUNNING, RunState.VERIFYING)
        self.assertEqual(illegal_transitions()["count"], 0)

    def test_every_terminal_escape_attempt_is_caught(self) -> None:
        # Terminal immutability is the invariant most worth proving observable:
        # a finished run being restarted is the textbook "it said X but did Y".
        for terminal in TERMINAL_STATES:
            with self.subTest(terminal=terminal):
                reset_illegal_transitions()
                transition(terminal, RunState.RUNNING, source="test")
                self.assertEqual(illegal_transitions()["count"], 1)

    def test_the_record_is_bounded_but_the_count_is_not(self) -> None:
        # A capped list that silently drops the earliest evidence would recreate
        # the blindness this exists to remove, so the true total is kept apart.
        for _ in range(200):
            transition(RunState.COMPLETED, RunState.RUNNING)
        record = illegal_transitions()
        self.assertEqual(record["count"], 200)
        self.assertLessEqual(len(record["recent"]), 64)

    def test_the_source_label_cannot_carry_arbitrary_text(self) -> None:
        # This record is read by diagnostics; it must never become a place a
        # provider string or a prompt fragment can land.
        transition(
            RunState.COMPLETED,
            RunState.RUNNING,
            source="api_key=sk-secret value; DROP TABLE",
        )
        source = illegal_transitions()["recent"][0]["source"]
        self.assertNotIn(" ", source)
        self.assertNotIn(";", source)
        self.assertTrue(re.fullmatch(r"[a-z0-9._-]+", source), source)

    def test_recording_is_thread_safe(self) -> None:
        # The pipeline runs turns on worker threads; a torn counter would make
        # the gate's number meaningless.
        def hammer() -> None:
            for _ in range(100):
                transition(RunState.COMPLETED, RunState.RUNNING)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(illegal_transitions()["count"], 800)

    def test_the_returned_record_cannot_be_mutated_from_outside(self) -> None:
        transition(RunState.COMPLETED, RunState.RUNNING)
        record = illegal_transitions()
        record["recent"][0]["from"] = "tampered"
        record["recent"].append({"from": "invented", "to": "nonsense"})
        fresh = illegal_transitions()
        self.assertEqual(fresh["recent"][0]["from"], "completed")
        self.assertEqual(len(fresh["recent"]), 1)


class BrowserParityTests(unittest.TestCase):
    """The same violation must be observable on the surface that runs live."""

    def test_the_js_store_also_records_refusals(self) -> None:
        source = _MESSAGE_STATE_JS.read_text(encoding="utf-8")
        self.assertIn("illegalTransitions", source)
        self.assertIn("resetIllegalTransitions", source)

    def test_the_js_store_still_refuses_the_edge(self) -> None:
        # Recording must be additive: the refusal itself is the safety property.
        source = _MESSAGE_STATE_JS.read_text(encoding="utf-8")
        self.assertIn("return message;", source)


class PipelineCleanlinessTests(unittest.TestCase):
    """Gate 7's actual target: the number should be zero in real use."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        reset_illegal_transitions()

    def tearDown(self) -> None:
        reset_illegal_transitions()
        self._tmp.cleanup()

    def _run(self, result: dict) -> None:
        from opaihub.gui_pipeline import handle_gui_message

        with mock.patch("opaihub.ask.run_ask", return_value=result):
            handle_gui_message(
                self.root, "explain this repo", model_id="auto", mode="ask"
            )

    def test_an_ordinary_answered_turn_violates_nothing(self) -> None:
        self._run({"status": "answered_locally", "answer": "Here you go."})
        self.assertEqual(illegal_transitions()["count"], 0)

    def test_a_failed_turn_violates_nothing(self) -> None:
        self._run({"status": "runner_error", "error": "boom"})
        self.assertEqual(illegal_transitions()["count"], 0)

    def test_an_awaiting_turn_violates_nothing(self) -> None:
        self._run({"status": "confirmation_required", "message": "send?"})
        self.assertEqual(illegal_transitions()["count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
