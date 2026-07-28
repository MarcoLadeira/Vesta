"""A run waiting for the user is waiting, not finished (#295).

The defect this closes: every turn that handed control back to the user — a
command approval, an edit approval, a cloud consent, a model choice — derived
its lifecycle state from the completion verdict and so reported ``blocked``.
``blocked`` is a *terminal, immutable* state, but the user's next click resumes
that same work. So an ordinary "shall I run `pytest -q`?" was recorded in
history, receipts and the ledger as a run that could not proceed, and
``is_terminal()`` answered True for a run that was about to continue.

That is the epic's invariant 12 (honest uncertainty) and its ``waiting_user`` /
``awaiting_approval`` lifecycle states. It also matters for the product's
promise not to limit interaction: an interaction point must not be recorded as
a dead end, or asking the user starts to look like failing them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub.gui_pipeline import handle_gui_message
from opaihub.run_state import (
    AWAITING_INPUT_STATUSES,
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    RunState,
    can_transition,
    canonical_for,
    is_awaiting_input,
    is_terminal,
    label,
)


class StateMachineTests(unittest.TestCase):
    def test_waiting_for_the_user_is_not_terminal(self) -> None:
        self.assertFalse(is_terminal(RunState.AWAITING_INPUT))
        self.assertIn(RunState.AWAITING_INPUT, NON_TERMINAL_STATES)
        self.assertNotIn(RunState.AWAITING_INPUT, TERMINAL_STATES)

    def test_a_requested_cancel_is_not_yet_a_cancellation(self) -> None:
        # The acknowledgement must be immediate and honest without claiming
        # teardown already finished.
        self.assertFalse(is_terminal(RunState.CANCEL_REQUESTED))
        self.assertNotEqual(RunState.CANCEL_REQUESTED, RunState.CANCELLED)

    def test_any_live_phase_can_stop_to_ask(self) -> None:
        for state in (RunState.QUEUED, RunState.PREPARING, RunState.RUNNING):
            with self.subTest(state=state):
                self.assertTrue(can_transition(state, RunState.AWAITING_INPUT))

    def test_answering_resumes_the_work(self) -> None:
        # The whole point: the run continues, it does not restart as a new one.
        for state in (RunState.QUEUED, RunState.PREPARING, RunState.RUNNING):
            with self.subTest(state=state):
                self.assertTrue(can_transition(RunState.AWAITING_INPUT, state))

    def test_answering_never_jumps_straight_into_verification(self) -> None:
        # Verification judges evidence produced by work. Resuming has to redo
        # the work first, so the answer cannot skip to judging it.
        self.assertFalse(can_transition(RunState.AWAITING_INPUT, RunState.VERIFYING))

    def test_a_waiting_run_can_still_end(self) -> None:
        # The user can cancel, abandon, or the wait can time out.
        for terminal in TERMINAL_STATES:
            with self.subTest(terminal=terminal):
                self.assertTrue(can_transition(RunState.AWAITING_INPUT, terminal))

    def test_a_requested_cancel_only_ends(self) -> None:
        self.assertFalse(can_transition(RunState.CANCEL_REQUESTED, RunState.RUNNING))
        self.assertTrue(can_transition(RunState.CANCEL_REQUESTED, RunState.CANCELLED))

    def test_stop_losing_the_race_is_reported_honestly(self) -> None:
        # Pressing Stop while the last step was already finishing is a race.
        # Reporting that as cancelled would misstate what actually happened.
        self.assertTrue(can_transition(RunState.CANCEL_REQUESTED, RunState.COMPLETED))

    def test_a_finished_run_can_never_start_waiting(self) -> None:
        for terminal in TERMINAL_STATES:
            with self.subTest(terminal=terminal):
                self.assertFalse(
                    can_transition(terminal, RunState.AWAITING_INPUT),
                    "terminal states are immutable",
                )

    def test_the_label_names_the_user_not_a_stuck_run(self) -> None:
        self.assertEqual(label(RunState.AWAITING_INPUT), "Waiting for you")
        self.assertEqual(label(RunState.CANCEL_REQUESTED), "Stopping")

    def test_the_epics_own_state_words_resolve_here(self) -> None:
        # A surface or document written against #295's vocabulary converges on
        # this machine instead of starting a rival one.
        for alias in ("waiting_user", "awaiting_approval", "awaiting_input"):
            with self.subTest(alias=alias):
                self.assertIs(canonical_for(alias), RunState.AWAITING_INPUT)
        self.assertIs(canonical_for("cancel_requested"), RunState.CANCEL_REQUESTED)

    def test_every_awaiting_status_is_recognised(self) -> None:
        for status in AWAITING_INPUT_STATUSES:
            with self.subTest(status=status):
                self.assertTrue(is_awaiting_input(status))

    def test_ordinary_statuses_are_not_mistaken_for_waiting(self) -> None:
        for status in (
            "answered",
            "failed",
            "cancelled",
            "blocked",
            "",
            "runner_error",
        ):
            with self.subTest(status=status):
                self.assertFalse(is_awaiting_input(status))

    def test_needing_a_configured_model_is_a_dead_end_not_a_question(self) -> None:
        # The membership test is "does the user's answer resume this run?".
        # `needs_model` means Auto found nothing it could call; the remedy is to
        # configure a provider and start again. Filing it as awaiting would
        # dress a genuine dead end up as a question.
        self.assertFalse(is_awaiting_input("needs_model"))

    def test_every_awaiting_status_is_resumed_by_an_added_authority(self) -> None:
        # The property that makes AWAITING_INPUT honest: each of these is
        # re-sent as the same task plus one grant, so the run continues.
        self.assertEqual(
            AWAITING_INPUT_STATUSES,
            {
                "needs_command_approval",
                "needs_edit_approval",
                "needs_free_confirmation",
                "needs_auto_confirmation",
                "needs_limit_confirmation",
                "needs_confirmation",
            },
        )


class HumanLanguageTests(unittest.TestCase):
    """Layer 1 speaks English; Layer 2 keeps the state names (#295).

    The product amendment is explicit: user-facing text says "I need permission
    to push this branch", never an internal state such as `awaiting_approval`.
    The strict runtime exists so the conversation can stay natural — leaking the
    state machine into the chat is the failure mode it is meant to prevent.
    """

    # Internal vocabulary that must never reach a rendered string.
    INTERNAL_WORDS = (
        "awaiting_input",
        "awaiting_approval",
        "waiting_user",
        "cancel_requested",
        "run_state",
        "needs_",
        "_confirmation",
        "_approval",
    )

    def test_no_awaiting_question_leaks_internal_vocabulary(self) -> None:
        from opaihub.gui_pipeline import _AWAITING_ASKS

        for status, (_kind, question) in _AWAITING_ASKS.items():
            with self.subTest(status=status):
                lowered = question.lower()
                for word in self.INTERNAL_WORDS:
                    self.assertNotIn(word, lowered, f"{status} leaks {word!r}")

    def test_every_ask_reads_as_opai_speaking(self) -> None:
        from opaihub.gui_pipeline import _AWAITING_ASKS

        for status, (_kind, question) in _AWAITING_ASKS.items():
            with self.subTest(status=status):
                # First person and a real sentence, not a field label.
                self.assertTrue(question.startswith("I "), question)
                self.assertTrue(question.endswith("."), question)

    def test_the_state_label_is_english_not_the_state_name(self) -> None:
        for state in (RunState.AWAITING_INPUT, RunState.CANCEL_REQUESTED):
            with self.subTest(state=state):
                rendered = label(state)
                self.assertNotIn("_", rendered)
                self.assertNotEqual(rendered.lower(), state.value)

    def test_the_machine_readable_kind_stays_a_closed_vocabulary(self) -> None:
        from opaihub.gui_pipeline import _AWAITING_ASKS

        kinds = {kind for kind, _q in _AWAITING_ASKS.values()}
        self.assertTrue(kinds <= {"approval", "consent", "choice", "confirmation"})


class PipelineTests(unittest.TestCase):
    """Proven through the real pipeline, not just the state machine."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_cloud_consent_turn_is_waiting_not_blocked(self) -> None:
        with mock.patch(
            "opaihub.ask.run_ask",
            return_value={"status": "confirmation_required", "message": "send?"},
        ):
            result = handle_gui_message(
                self.root, "explain this repo", model_id="auto", mode="ask"
            )
        self.assertEqual(result["status"], "needs_auto_confirmation")
        self.assertEqual(result["run_state"], RunState.AWAITING_INPUT.value)
        self.assertFalse(is_terminal(result["run_state"]))

    def test_a_command_approval_turn_is_waiting_not_blocked(self) -> None:
        approval = {
            "status": "answered_by_free_api",
            "answer": "",
            "command_approval": {"command": "pytest -q", "reason": "confirm-class"},
        }
        with mock.patch("opai.app_state.ask", return_value=approval):
            result = handle_gui_message(
                self.root,
                "fix the failing test",
                model_id="free:gemini:x",
                mode="safe-auto",
                allow_cloud=True,
            )
        self.assertEqual(result["status"], "needs_command_approval")
        self.assertEqual(result["run_state"], RunState.AWAITING_INPUT.value)

    def test_the_waiting_run_says_what_it_is_waiting_for(self) -> None:
        approval = {
            "status": "answered_by_free_api",
            "answer": "",
            "command_approval": {"command": "pytest -q", "reason": "confirm-class"},
        }
        with mock.patch("opai.app_state.ask", return_value=approval):
            result = handle_gui_message(
                self.root,
                "fix the failing test",
                model_id="free:gemini:x",
                mode="safe-auto",
                allow_cloud=True,
            )
        awaiting = result["awaiting"]
        self.assertEqual(awaiting["kind"], "approval")
        self.assertTrue(awaiting["question"])
        self.assertTrue(awaiting["resumable"])
        # Explicitly stated rather than omitted: nothing keeps spending while
        # the question is on screen.
        self.assertFalse(awaiting["backgroundActive"])

    def test_a_finished_run_carries_no_awaiting_payload(self) -> None:
        with mock.patch(
            "opaihub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "Here you go."},
        ):
            result = handle_gui_message(
                self.root, "explain this repo", model_id="auto", mode="ask"
            )
        self.assertNotIn("awaiting", result)
        self.assertTrue(is_terminal(result["run_state"]))

    def test_a_genuine_failure_is_still_terminal(self) -> None:
        # The fix must not turn real dead ends into permanent "waiting". Auto
        # with nothing runnable ends `needs_model`, which stays terminal.
        with (
            mock.patch(
                "opaihub.ask.run_ask",
                return_value={"status": "runner_error", "error": "boom"},
            ),
            mock.patch("opai.app_state.available_models", return_value={"models": []}),
        ):
            result = handle_gui_message(
                self.root, "explain this repo", model_id="auto", mode="ask"
            )
        self.assertNotEqual(result["run_state"], RunState.AWAITING_INPUT.value)
        self.assertTrue(is_terminal(result["run_state"]))
        self.assertNotIn("awaiting", result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
