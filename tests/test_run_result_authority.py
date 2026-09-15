"""#618: the canonical RunResult is the only thing allowed to say what a turn meant.

Before this, each surface reached into ``completion_verdict`` -- or, worse, a
legacy status string -- and decided for itself. The failure that proves why that
is not merely untidy is regression-tested below: a run whose verdict was
``needs_attention`` was persisted as ``complete`` purely because its legacy
status happened to be ``answered_by_account``. Nothing about the run had
changed; the surface simply asked the wrong authority.

The rules these tests hold:

1. a canonical RunResult outranks everything else;
2. the #378 verdict is consulted only when no canonical result exists;
3. a legacy status may never report success -- at most it narrows an already
   unknown result to a failure-shaped one.
"""

from __future__ import annotations

import unittest

from vesta.gui_recents import thread_status_for_result
from vestahub.generated_lifecycle import TERMINAL_STATE_IDS
from vestahub.run_result import RunResult, terminal_presentation


def _result(state: str) -> dict:
    """A full canonical payload, not a hand-shaped stub.

    `terminal_presentation` validates before it answers and fails closed to
    needs_attention on anything it cannot read. A bare
    {"lifecycle": {"state": ...}} is therefore rejected -- correctly, since it
    is indistinguishable from a corrupted record -- so the fixtures build real
    results through the projector's own contract.
    """

    extra: dict = {}
    if state == "completed":
        # A completed result must carry verification, delivery and reconciled
        # cost evidence -- the schema refuses to build one without them, which
        # is how "finished, but we cannot account for it" is made unwritable.
        extra = {
            "verification": {"applicable": False, "verdict": "not_applicable"},
            "delivery": {
                "applicable": True,
                "verdict": "delivered",
                "record_ref": {"kind": "turn_record", "id": "t"},
            },
            "economics": {
                "integrity": "reconciled",
                "record_ref": {"kind": "ledger_event", "id": "e"},
            },
        }
    return RunResult.from_payload(
        state=state,
        reason_detail=f"scenario: {state}",
        final_transition_at="2026-08-11T12:00:00+00:00",
        mutating=False,
        **extra,
    ).to_dict()


class CanonicalStateAccessorTests(unittest.TestCase):
    """`terminal_presentation` is the one downstream result accessor (#618).

    An earlier revision of this branch introduced a second accessor. PR #698
    landed `terminal_presentation` first and it is stricter -- it fails closed
    on any payload it cannot validate -- so it is canonical and the competing
    accessor was removed rather than kept as an alternative. Two ways to ask
    what a run meant is the defect this issue exists to end.
    """

    def test_a_canonical_result_yields_its_terminal_state(self) -> None:
        for state in sorted(TERMINAL_STATE_IDS):
            with self.subTest(state=state):
                self.assertEqual(terminal_presentation(_result(state)).state, state)

    def test_a_real_run_result_object_is_accepted(self) -> None:
        built = RunResult.from_payload(
            state="partial",
            reason_detail="verification incomplete",
            final_transition_at="2026-01-01T00:00:00+00:00",
            mutating=False,
        )
        self.assertEqual(terminal_presentation(built.to_dict()).state, "partial")

    def test_absent_or_unusable_payloads_yield_no_state(self) -> None:
        """The accessor must never guess; a caller needs to know it has nothing.

        Returning a plausible-looking state here would put the fabrication
        inside the canonical module, which is the worst possible place for it.
        """

        for payload in (None, {}, "completed", 42, {"lifecycle": None}, []):
            with self.subTest(payload=payload):
                # Fails *closed*: an unreadable payload becomes needs_attention,
                # never a plausible-looking success. That is stricter than
                # returning "nothing" and is why this accessor is canonical.
                self.assertEqual(
                    terminal_presentation(payload).state, "needs_attention"
                )

    def test_a_non_terminal_or_unknown_state_is_not_returned(self) -> None:
        for state in ("running", "queued", "definitely-not-a-state", ""):
            with self.subTest(state=state):
                self.assertEqual(
                    terminal_presentation({"lifecycle": {"state": state}}).state,
                    "needs_attention",
                )


class ThreadStatusAuthorityTests(unittest.TestCase):
    def test_needs_attention_is_never_reported_as_complete(self) -> None:
        """The exact production defect #618 exists to remove.

        `needs_attention` was missing from the hand-written verdict table, so it
        fell through to the legacy branch, where `answered_by_account` meant
        "complete". A run Vesta could not verify was recorded as a success.
        """

        self.assertEqual(
            thread_status_for_result(
                "answered_by_account", {"verdict": "needs_attention"}
            ),
            "needs_attention",
        )

    def test_every_terminal_state_maps_without_falling_through(self) -> None:
        """No terminal state may reach the legacy branch by omission.

        The table is derived from the schema precisely so a state added later
        cannot acquire a fall-through meaning that nobody chose.
        """

        for state in sorted(TERMINAL_STATE_IDS):
            with self.subTest(state=state):
                mapped = thread_status_for_result(
                    "answered_by_account", {"verdict": state}
                )
                expected = "complete" if state == "completed" else state
                self.assertEqual(mapped, expected)

    def test_the_canonical_result_outranks_the_verdict(self) -> None:
        self.assertEqual(
            thread_status_for_result(
                "answered_by_account", {"verdict": "completed"}, _result("partial")
            ),
            "partial",
        )

    def test_the_canonical_result_outranks_a_legacy_success_string(self) -> None:
        self.assertEqual(
            thread_status_for_result("answered_locally", None, _result("failed")),
            "failed",
        )

    def test_a_legacy_status_alone_cannot_claim_completion(self) -> None:
        """Transport is not engineering completion.

        "The provider answered" is all these old records preserve. It says
        nothing about whether the work was verified, so the honest import is
        "cannot determine", not "complete".
        """

        for legacy in (
            "answered",
            "cache_hit",
            "answered_by_account",
            "answered_by_free_api",
            "answered_locally",
            "applied",
            "no_edits",
        ):
            with self.subTest(legacy=legacy):
                self.assertEqual(
                    thread_status_for_result(legacy, None), "needs_attention"
                )

    def test_an_unknown_verdict_degrades_explicitly(self) -> None:
        for verdict in ("wat", "succeeded", "answered_by_account", "TIMEOUT_?"):
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    thread_status_for_result("answered", {"verdict": verdict}),
                    "needs_attention",
                )

    def test_cancellation_and_failure_still_import(self) -> None:
        self.assertEqual(thread_status_for_result("cancelled", None), "cancelled")
        self.assertEqual(thread_status_for_result("error", None), "failed")

    def test_no_legacy_input_can_produce_complete(self) -> None:
        """The property that matters, stated directly rather than by example.

        Whatever combination of legacy status and unrecognised verdict arrives,
        "complete" must be unreachable without canonical evidence.
        """

        legacy_statuses = [
            "answered",
            "cache_hit",
            "answered_by_account",
            "answered_by_free_api",
            "answered_locally",
            "applied",
            "no_edits",
            "cancelled",
            "error",
            "",
            "totally-unknown",
        ]
        verdicts = [None, {}, {"verdict": ""}, {"verdict": "unknown-value"}]
        for status in legacy_statuses:
            for verdict in verdicts:
                with self.subTest(status=status, verdict=verdict):
                    self.assertNotEqual(
                        thread_status_for_result(status, verdict),
                        "complete",
                        "a legacy string manufactured completion with no "
                        "canonical evidence behind it",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class BackgroundRunAuthorityTests(unittest.TestCase):
    """A background run must reach the same verdict a foreground turn would.

    Background execution was the surface most able to drift: it runs without a
    watching user, persists its own record, and drives notifications. If it
    ranked its local signals differently from the GUI, the same evidence would
    be reported two ways and only one of them could be right.
    """

    def _classify(self, payload: dict, *, cancelled: bool = False):
        from vestahub.background_runs import _terminal_from_payload

        return _terminal_from_payload(payload, cancelled=cancelled)

    def test_a_legacy_only_payload_cannot_complete_a_background_run(self) -> None:
        """`{"status": "answered"}` is transport, not engineering completion.

        This branch is reached only by a record with no canonical result, no
        run_state and no verdict -- something production stopped producing when
        background runs began projecting a RunResult. Importing it as COMPLETED
        would claim verification that never happened.
        """

        from vestahub.run_state import RunState

        for legacy in ("answered", "completed", "ok", "success"):
            with self.subTest(legacy=legacy):
                state, reason, _ = self._classify({"status": legacy})
                self.assertEqual(state, RunState.NEEDS_ATTENTION)
                self.assertEqual(reason, "background_legacy_status_unverifiable")

    def test_the_canonical_result_decides_over_a_legacy_status(self) -> None:
        from vestahub.run_state import RunState

        state, _, _ = self._classify(
            {"status": "answered", "run_result": _result("partial")}
        )
        self.assertEqual(state, RunState.PARTIAL)

    def test_background_and_history_agree_on_the_same_evidence(self) -> None:
        """The cross-surface invariant, on the two surfaces migrated so far.

        Same canonical result in, same canonical meaning out -- regardless of
        which surface observed it or what legacy status rode alongside.
        """

        for state in ("completed", "partial", "failed", "cancelled", "needs_attention"):
            with self.subTest(state=state):
                payload = {
                    "status": "answered_by_account",
                    "run_result": _result(state),
                }
                background, _, _ = self._classify(payload)
                history = thread_status_for_result(
                    payload["status"], None, payload["run_result"]
                )
                expected_history = "complete" if state == "completed" else state
                self.assertEqual(background.value, state)
                self.assertEqual(history, expected_history)


class NotificationAuthorityTests(unittest.TestCase):
    """A notification may be terse. It may not disagree.

    #618 lists notifications among the surfaces that must consume the same
    result, and they are the easiest place for a divergence to hide: nobody is
    watching when a background run posts one, and "Completed" is a comfortable
    default. The notification carries run_state verbatim from the classifier,
    so it cannot say more than the canonical result does.
    """

    def test_a_notification_reports_the_canonical_state_verbatim(self) -> None:
        import inspect

        from vestahub import background_runs

        source = inspect.getsource(background_runs._notify)
        self.assertIn(
            '"run_state": run.run_state',
            source,
            "the notification stopped copying the canonical state and now "
            "derives its own -- that is a second result authority",
        )
        self.assertNotIn(
            '"Completed"',
            source,
            "the notification hard-codes a success word instead of reporting "
            "the canonical state",
        )

    def test_a_partial_run_never_notifies_as_completed(self) -> None:
        """The concrete divergence this prevents, stated as behaviour."""

        from vestahub.background_runs import _terminal_from_payload

        state, _, summary = _terminal_from_payload(
            {"status": "answered_by_account", "run_result": _result("partial")},
            cancelled=False,
        )
        self.assertEqual(state.value, "partial")
        self.assertNotIn("completed", summary.lower())
