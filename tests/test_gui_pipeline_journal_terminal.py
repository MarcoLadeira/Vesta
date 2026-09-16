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
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from vestahub import gui_pipeline, journal_runtime
from vestahub.cost_telemetry import normalize_account_result
from vestahub.journal_runtime import (
    EVENT_ADMITTED,
    EVENT_CANCELLED,
    EVENT_FINISHED,
    record_admission,
)
from vestahub.journal_store import journal_path, open_store, read_events

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
        # The engine's own canonical state, as every decorated result carries.
        self._run_wrapper(
            result={"status": "completed", "run_state": "completed", "answer": "done"}
        )

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


class UnknownStatusesAreNotGuessedTests(_TerminalFixture):
    """A fabricated verdict would look exactly like a real contradiction.

    Stage 4 compares journal against legacy. Inventing "failed" for a status
    nobody mapped would produce a difference that is entirely this module's
    fault, and would be indistinguishable from the real divergences the
    comparison exists to surface.

    That reasoning was right and this class used to stop halfway through it.
    It concluded that the ending should therefore be recorded as *completed* --
    which is equally fabricated, and fabricated in the direction that matters,
    because #818's closing evidence has to show zero false completion. Seven
    of the status strings the pipeline actually emits fell through that
    default, `timeout` and `provider_blocked` among them.

    ``unknown`` is the answer the argument actually supports: not a success,
    not a failure, so it cannot invent a contradiction of either kind. The run
    ended; how it ended is not known.
    """

    def test_an_unrecognised_status_is_recorded_as_unknown(self):
        self._run_wrapper(result={"status": "some_new_status_nobody_mapped"})

        self.assertEqual(self._verdict(), "unknown")

    def test_an_unrecognised_status_is_not_recorded_as_a_success(self):
        self._run_wrapper(result={"status": "some_new_status_nobody_mapped"})

        self.assertNotEqual(self._verdict(), "completed")

    def test_an_unrecognised_status_is_not_recorded_as_a_failure(self):
        self._run_wrapper(result={"status": "some_new_status_nobody_mapped"})

        self.assertNotEqual(self._verdict(), "failed")

    def test_a_result_with_no_status_is_not_a_completion(self):
        """#818 review finding 15: the wrapper defaulted a missing status to
        "completed". A turn that says nothing about how it ended did not
        thereby succeed."""

        self._run_wrapper(result={"answer": "done"})

        self.assertEqual(self._verdict(), "unknown")

    def test_a_none_result_does_not_crash_the_wrapper(self):
        self._run_wrapper(result=None)

        # Ended, unnamed -- never a success, and never left running.
        self.assertEqual(self._verdict(), "unknown")

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


class TheJournalRecordsHowWellItKnowsACostTests(unittest.TestCase):
    """#818: "unknown cost is never represented as zero", in the store itself.

    `CostTelemetry` already reports how the number was arrived at --
    `normalize_account_result` returns "actual" when a provider gave a real
    dollar figure (Claude does) and "estimated" when it did not (Codex). The
    mirror hard-coded "estimated" over the top, which is why every cost event
    in a real journal reads as an estimate and not one reads as actual.

    The zero mattered more. `cost_usd` is deliberately `None` for a call whose
    price nobody measured, and `or 0.0` turned that into a recorded $0.00. The
    store has had an `unavailable` measurement kind since v1, with no writer.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fence = record_admission(
            self.root,
            task_id="t",
            run_id="r1",
            task="a turn",
            now="2026-09-08T10:00:00+00:00",
            surface="gui",
        )

    def _record(self, telemetry, *, key: str) -> None:
        token = gui_pipeline._JOURNAL_RUN.set({"run_id": "r1", "fence": self.fence})
        try:
            gui_pipeline._journal_cost(self.root, telemetry, operation_key=key)
        finally:
            gui_pipeline._JOURNAL_RUN.reset(token)

    def _cost_rows(self):
        connection = sqlite3.connect(journal_path(self.root))
        connection.row_factory = sqlite3.Row
        try:
            return {
                str(row["operation_key"]): (
                    float(row["amount"]),
                    str(row["measurement_kind"]),
                )
                for row in connection.execute(
                    "SELECT operation_key, amount, measurement_kind FROM cost_events"
                )
            }
        finally:
            connection.close()

    def test_a_measured_cost_is_recorded_as_actual(self):
        self._record(
            normalize_account_result("claude", {"cost_usd": 0.0421}, model="sonnet"),
            key="r1:claude",
        )

        self.assertEqual(self._cost_rows()["r1:claude"], (0.0421, "actual"))

    def test_an_unmeasured_cost_is_not_recorded_as_zero_spend(self):
        """The one that matters. A provider that reported no dollars must not
        be filed as having cost nothing."""

        self._record(
            normalize_account_result("codex", {"tokens": 900}, model="gpt-5"),
            key="r1:codex",
        )

        _amount, kind = self._cost_rows()["r1:codex"]
        self.assertEqual(kind, "unavailable")
        self.assertNotEqual(kind, "estimated")

    def test_a_genuine_zero_is_still_a_measurement(self):
        """A provider that reported exactly $0.00 measured something."""

        self._record(
            normalize_account_result("claude", {"cost_usd": 0.0}, model="sonnet"),
            key="r1:free",
        )

        self.assertEqual(self._cost_rows()["r1:free"], (0.0, "actual"))

    def test_the_kind_is_one_the_store_accepts(self):
        """A kind outside the store's vocabulary is refused at the boundary,
        so a typo here would silently drop the cost instead of raising."""

        for key, telemetry in (
            ("a", normalize_account_result("claude", {"cost_usd": 1.5})),
            ("b", normalize_account_result("codex", {})),
        ):
            with self.subTest(key=key):
                self._record(telemetry, key=f"r1:{key}")
                self.assertIn(key := f"r1:{key}", self._cost_rows(), key)


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
