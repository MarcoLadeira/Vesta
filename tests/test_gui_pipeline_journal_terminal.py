"""#613 Stage 3: every turn ends exactly once in the journal, on every exit.

Admission was wired in #743. This is the other half, and it needed a choke
point rather than a set of hooks.

``_handle_gui_message`` has more than a dozen returns spread through deeply
nested closures. Hooking each one would mean a missed path silently loses a
run's ending -- the precise failure #613 exists to remove, and one that no test
would notice because the turn itself still works. A wrapper has one exit by
construction, and catches the case no return-site hook could: a turn that
raises.

So these tests care about *exits*, not about the pipeline's behaviour. They
drive the wrapper with a stubbed implementation, because the question is
"whatever the implementation does, is the run closed out?" -- and a stub can
return, raise, and return odd shapes on demand in a way a real turn cannot.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import gui_pipeline, journal_runtime
from opaihub.journal_runtime import EVENT_ADMITTED, EVENT_CANCELLED, EVENT_FINISHED
from opaihub.journal_store import open_store, read_events

NOW = "2026-08-25T12:00:00+00:00"
_UNSET = object()


class _TerminalFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fence = journal_runtime.record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task="fix the failing login test",
            now=NOW,
        )

    def _run_wrapper(self, *, result=None, error: BaseException | None = None):
        """Drive the public wrapper with a stubbed implementation."""

        def fake(*args, **kwargs):
            gui_pipeline._JOURNAL_RUN.set({"run_id": "run-a", "fence": self.fence})
            if error is not None:
                raise error
            return result

        with mock.patch.object(gui_pipeline, "_handle_gui_message", fake):
            return gui_pipeline.handle_gui_message(self.root, "a message")

    def _events(self) -> list[str]:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return [row["event_type"] for row in read_events(store)]

    def _verdict(self) -> str | None:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store.execute("SELECT terminal_verdict FROM runs").fetchone()[0]


class EveryExitClosesTheRunTests(_TerminalFixture):
    def test_a_completed_turn_records_a_finish(self):
        self._run_wrapper(result={"status": "completed", "answer": "done"})

        self.assertEqual(self._events(), [EVENT_ADMITTED, EVENT_FINISHED])
        self.assertEqual(self._verdict(), "completed")

    def test_a_cancelled_turn_records_a_cancellation(self):
        self._run_wrapper(result={"status": "cancelled"})

        self.assertEqual(self._events(), [EVENT_ADMITTED, EVENT_CANCELLED])
        self.assertEqual(self._verdict(), "cancelled")

    def test_a_failed_turn_records_a_failure(self):
        self._run_wrapper(result={"status": "failed", "reason": "provider_down"})

        self.assertEqual(self._verdict(), "failed")

    def test_a_raising_turn_still_closes_the_run(self):
        """The exit no return-site hook could ever catch."""

        with self.assertRaises(RuntimeError):
            self._run_wrapper(error=RuntimeError("boom"))

        self.assertEqual(self._events(), [EVENT_ADMITTED, EVENT_FINISHED])
        self.assertEqual(self._verdict(), "failed")

    def test_a_keyboard_interrupt_still_closes_the_run(self):
        """BaseException, not Exception: a Ctrl-C leaves a run behind too."""

        with self.assertRaises(KeyboardInterrupt):
            self._run_wrapper(error=KeyboardInterrupt())

        self.assertEqual(self._verdict(), "failed")

    def test_a_turn_that_ends_exactly_once_does_not_double_record(self):
        self._run_wrapper(result={"status": "completed"})

        self.assertEqual(self._events().count(EVENT_FINISHED), 1)


class UnknownStatusesAreNotGuessedAsFailuresTests(_TerminalFixture):
    """A fabricated verdict would look exactly like a real contradiction.

    Stage 4 compares journal against legacy. Inventing "failed" for a status
    nobody mapped would produce a difference that is entirely this module's
    fault, and would be indistinguishable from the real divergences the
    comparison exists to surface.
    """

    def test_an_unrecognised_status_is_treated_as_a_completion(self):
        self._run_wrapper(result={"status": "some_new_status_nobody_mapped"})

        self.assertEqual(self._verdict(), "completed")

    def test_a_result_with_no_status_is_treated_as_a_completion(self):
        self._run_wrapper(result={"answer": "done"})

        self.assertEqual(self._verdict(), "completed")

    def test_a_none_result_does_not_crash_the_wrapper(self):
        self._run_wrapper(result=None)

        self.assertEqual(self._verdict(), "completed")

    def test_a_duplicate_request_is_recorded_as_such(self):
        """Not an error and not a fresh run: it attached to one in flight."""

        self._run_wrapper(result={"status": "duplicate_request"})

        self.assertEqual(self._verdict(), "duplicate")


class TheRunIdentityDoesNotLeakTests(_TerminalFixture):
    """A ContextVar that is not reset would close out the *next* turn's run."""

    def test_the_context_is_cleared_after_a_successful_turn(self):
        self._run_wrapper(result={"status": "completed"})

        self.assertIsNone(gui_pipeline._JOURNAL_RUN.get())

    def test_the_context_is_cleared_after_a_raising_turn(self):
        with self.assertRaises(RuntimeError):
            self._run_wrapper(error=RuntimeError("boom"))

        self.assertIsNone(gui_pipeline._JOURNAL_RUN.get())

    def test_a_turn_that_never_admitted_records_nothing(self):
        """No journal identity means there is no run to close."""

        def fake(*args, **kwargs):
            return {"status": "completed"}

        with mock.patch.object(gui_pipeline, "_handle_gui_message", fake):
            gui_pipeline.handle_gui_message(self.root, "a message")

        self.assertEqual(self._events(), [EVENT_ADMITTED])
        self.assertIsNone(self._verdict())


class BookkeepingNeverFailsAFinishedTurnTests(_TerminalFixture):
    """The rule the whole of Stage 3 is written under."""

    def test_a_broken_terminal_write_does_not_lose_the_answer(self):
        with mock.patch.object(
            journal_runtime, "record_terminal", side_effect=OSError("disk full")
        ):
            result = self._run_wrapper(
                result={"status": "completed", "answer": "the answer"}
            )

        self.assertEqual(result["answer"], "the answer")

    def test_a_broken_terminal_write_does_not_mask_a_real_exception(self):
        """The turn's own failure must still reach the caller."""

        with mock.patch.object(
            journal_runtime, "record_terminal", side_effect=OSError("disk full")
        ):
            with self.assertRaises(RuntimeError):
                self._run_wrapper(error=RuntimeError("the real problem"))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class CostAndVerificationWiringTests(_TerminalFixture):
    """Stage 3's last two surfaces, checked through the pipeline's own helpers.

    The helpers are exercised directly rather than through a real turn: a turn
    that reaches the paid route needs a provider, and the question here is not
    "does the pipeline call a provider" but "when a cost or a verdict exists,
    does it reach the journal exactly once".
    """

    class _Telemetry:
        def __init__(self, cost_usd: float, model: str, tokens: int) -> None:
            self.cost_usd = cost_usd
            self.model = model
            self.tokens = tokens

    @contextlib.contextmanager
    def _with_identity(self, identity=_UNSET):
        """Set the run identity the way the pipeline itself does.

        A ContextVar's ``get`` is read-only and cannot be patched -- but a
        ContextVar is built to be set, so using it directly is both simpler and
        closer to what the real code does.
        """

        value = (
            {"run_id": "run-a", "fence": self.fence} if identity is _UNSET else identity
        )
        token = gui_pipeline._JOURNAL_RUN.set(value)
        try:
            yield
        finally:
            gui_pipeline._JOURNAL_RUN.reset(token)

    def test_a_priced_call_is_journalled_once(self):
        with self._with_identity():
            gui_pipeline._journal_cost(
                self.root,
                self._Telemetry(0.25, "sonnet", 1200),
                operation_key="turn-1:account",
            )
            gui_pipeline._journal_cost(
                self.root,
                self._Telemetry(0.25, "sonnet", 1200),
                operation_key="turn-1:account",
            )

        store = open_store(self.root)
        self.addCleanup(store.close)
        rows = store.execute("SELECT COUNT(*), SUM(amount) FROM cost_events").fetchone()
        self.assertEqual(rows[0], 1, "a repeat must not charge twice")
        self.assertEqual(float(rows[1]), 0.25)

    def test_a_turn_with_no_journal_identity_records_no_cost(self):
        with self._with_identity(None):
            gui_pipeline._journal_cost(
                self.root, self._Telemetry(1.0, "m", 1), operation_key="x"
            )

        store = open_store(self.root)
        self.addCleanup(store.close)
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0], 0
        )

    def test_a_broken_cost_mirror_never_raises(self):
        with self._with_identity():
            with mock.patch.object(
                journal_runtime, "record_run_cost", side_effect=OSError("disk full")
            ):
                gui_pipeline._journal_cost(
                    self.root, self._Telemetry(1.0, "m", 1), operation_key="x"
                )

    def test_a_verification_records_its_policy_digest(self):
        with self._with_identity():
            gui_pipeline._journal_verification(
                self.root,
                {
                    "verdict": "passed",
                    "policy": {"digest": "a" * 64},
                    "artifact": {"digest": "b" * 64},
                },
            )

        store = open_store(self.root)
        self.addCleanup(store.close)
        verified = [
            row
            for row in read_events(store)
            if row["event_type"] == journal_runtime.EVENT_VERIFIED
        ]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["payload"]["policy_digest"], "a" * 64)

    def test_an_empty_manifest_records_nothing(self):
        with self._with_identity():
            gui_pipeline._journal_verification(self.root, {})

        store = open_store(self.root)
        self.addCleanup(store.close)
        verified = [
            row
            for row in read_events(store)
            if row["event_type"] == journal_runtime.EVENT_VERIFIED
        ]
        self.assertEqual(verified, [])
