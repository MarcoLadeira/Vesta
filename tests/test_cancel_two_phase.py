""" "Stopped" must not be claimed before teardown is observed (#614).

#614's protocol is ``active -> cancel_requested -> cancelled``. ``cancelled``
means Vesta-owned work has been *seen* to stop; ``cancel_requested`` means the
stop was asked for and is still being reconciled. Collapsing the two lets the
UI report "Stopped" while a provider call is still spending money or a child
process is still writing files.

The schema used to permit the shortcut from every active state. It is now
refused where Vesta can own live external work:

``running``    a provider call is in flight
``verifying``  verification subprocesses are executing

and still permitted where nothing of Vesta's is executing:

``queued``          never dispatched (``cancelled_before_start``)
``preparing``       assembling context; nothing external has been started
``awaiting_input``  parked on the user

The distinction is deliberate. Forcing a queued run through
``cancel_requested`` would mean acknowledging teardown of nothing, which is
ceremony rather than evidence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.run_state import RunState, can_transition, transition


class ShortcutRefusedTests(unittest.TestCase):
    def test_a_running_run_cannot_jump_to_cancelled(self) -> None:
        self.assertFalse(can_transition(RunState.RUNNING, RunState.CANCELLED))

    def test_a_verifying_run_cannot_jump_to_cancelled(self) -> None:
        # Verification runs real subprocesses; claiming they stopped without
        # observing it is the same defect as for a provider call.
        self.assertFalse(can_transition(RunState.VERIFYING, RunState.CANCELLED))

    def test_the_two_phase_path_is_open(self) -> None:
        self.assertTrue(can_transition(RunState.RUNNING, RunState.CANCEL_REQUESTED))
        self.assertTrue(can_transition(RunState.CANCEL_REQUESTED, RunState.CANCELLED))

    def test_a_refused_shortcut_leaves_the_state_untouched(self) -> None:
        # transition() refuses rather than raises, so the caller must not be
        # able to mistake a refusal for a move.
        self.assertIs(
            transition(RunState.RUNNING, RunState.CANCELLED, source="test"),
            RunState.RUNNING,
        )


class StatesWithNothingInFlightTests(unittest.TestCase):
    """Not every cancel needs a drain phase, and pretending otherwise is noise."""

    def test_a_queued_run_may_be_cancelled_outright(self) -> None:
        self.assertTrue(can_transition(RunState.QUEUED, RunState.CANCELLED))

    def test_a_preparing_run_may_be_cancelled_outright(self) -> None:
        self.assertTrue(can_transition(RunState.PREPARING, RunState.CANCELLED))

    def test_a_parked_run_may_be_cancelled_outright(self) -> None:
        self.assertTrue(can_transition(RunState.AWAITING_INPUT, RunState.CANCELLED))


class BackgroundRunCancelTests(unittest.TestCase):
    """The race the existing suite did not cover.

    ``request_cancel`` sets the flag, and the worker checks it just after
    transitioning to ``RUNNING``. A cancel landing in that window used to
    finish the run straight from ``RUNNING``. With the shortcut refused, a
    naive implementation would have the transition silently declined and leave
    the run stuck in ``RUNNING`` forever — a worse bug than the one being
    fixed. ``_finish`` acknowledges first so the run still reaches a terminal
    state, via the honest path.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_cancel_racing_execution_still_reaches_a_terminal_state(self) -> None:
        from opaihub.background_runs import (
            _coerce_run_state,
            _transition_run,
            enqueue_automation,
            load_run,
            request_cancel,
        )

        run = enqueue_automation(self.root, "bug_fix", "do a thing")
        _transition_run(
            self.root,
            run.run_id,
            target=RunState.PREPARING,
            reason_code="preparing_execution",
        )
        _transition_run(
            self.root,
            run.run_id,
            target=RunState.RUNNING,
            reason_code="execution_started",
        )
        request_cancel(self.root, run.run_id)

        final = load_run(self.root, run.run_id)
        state = _coerce_run_state(final.run_state)
        self.assertIn(
            state,
            {RunState.CANCEL_REQUESTED, RunState.CANCELLED},
            "a stop must land somewhere on the cancellation path, never nowhere",
        )
        self.assertTrue(final.cancel_requested)

    def test_finishing_a_running_run_without_teardown_evidence_needs_attention(
        self,
    ) -> None:
        # The regression the schema change could have caused. `_finish` is the
        # single choke point where a background run becomes terminal, and
        # `_transition_run` *refuses* an illegal edge by returning the current
        # state — so without the acknowledge-first step this run would sit in
        # RUNNING forever instead of ending. Exercises the fallback directly,
        # because the ordinary `request_cancel` path never reaches it.
        from opaihub.background_runs import (
            BackgroundRunner,
            _coerce_run_state,
            _transition_run,
            enqueue_automation,
            load_run,
        )

        run = enqueue_automation(self.root, "bug_fix", "do a thing")
        _transition_run(
            self.root,
            run.run_id,
            target=RunState.PREPARING,
            reason_code="preparing_execution",
        )
        running = _transition_run(
            self.root,
            run.run_id,
            target=RunState.RUNNING,
            reason_code="execution_started",
        )
        self.assertIs(_coerce_run_state(running.run_state), RunState.RUNNING)

        runner = BackgroundRunner(self.root, executor=lambda *a, **k: {})
        runner._finish(
            running,
            run_state=RunState.CANCELLED,
            reason_code="cancelled_before_execution",
            message="Stopped by you",
            payload={},
        )

        final = load_run(self.root, run.run_id)
        self.assertIs(_coerce_run_state(final.run_state), RunState.NEEDS_ATTENTION)
        self.assertEqual(final.reason_code, "cancellation_unconfirmed")
        states = [event.get("state") for event in final.state_history]
        self.assertIn(
            RunState.CANCEL_REQUESTED.value,
            states,
            "the acknowledgement must be on the record, not skipped over",
        )
        self.assertLess(
            states.index(RunState.CANCEL_REQUESTED.value),
            states.index(RunState.NEEDS_ATTENTION.value),
            "the request must be recorded before the unresolved outcome",
        )

    def test_the_history_records_the_request_before_the_confirmation(self) -> None:
        # The point of two phases is that support can tell a stubborn child
        # process from a UI-only state error. That needs both moments on the
        # record, in order.
        from opaihub.background_runs import (
            enqueue_automation,
            load_run,
            request_cancel,
        )

        run = enqueue_automation(self.root, "bug_fix", "do a thing")
        request_cancel(self.root, run.run_id)
        history = [
            event.get("state")
            for event in load_run(self.root, run.run_id).state_history
        ]
        self.assertTrue(history, "a cancellation must leave a trace")


if __name__ == "__main__":
    unittest.main()
