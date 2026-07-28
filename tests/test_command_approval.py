"""F17/F9 command-approval flow + F8/F14/F24 completion honesty contracts.

Covers the engine half of the cross-workstream contract:
- a NEEDS_CONSENT/command-approval tool result becomes a reply with
  ``status == "needs_command_approval"`` carrying the exact command + reason;
- ``allow_command``/``allowCommand`` on ``handle_gui_message`` threads the
  one-shot grant down to the executor unchanged;
- the account path emits a canonical ``completion_state`` derived from the
  runner's own terminal signals (is_error / subtype / permission denials);
- an edit-intent run with zero change evidence is never green-completed.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from _helpers import FakeAccountRunner, FakeStreamingRunner, make_repo

from opaihub.completion import (
    CompletionState,
    completion_state_from_legacy,
    result_is_completed,
)


@pytest.fixture(autouse=True)
def _clean_broken_git_config_env():
    """Scrub the broken inherited GIT_CONFIG_* header (see test_free_models)."""
    for name in list(os.environ):
        if name == "GIT_TERMINAL_PROMPT" or name.startswith("GIT_CONFIG_"):
            os.environ.pop(name, None)
    yield


class _NeedsConsentLoopRunner:
    """Free-model runner whose tool loop stops on a command-approval block."""

    name = "free-api"
    model = "m"
    last_usage = {}

    def available(self):
        return True

    def complete_with_tools(self, prompt, **kwargs):
        return {
            "text": "I need approval to run that command.",
            "tool_trace": [
                {
                    "tool": "run_command",
                    "ok": False,
                    "error_code": "COMMAND_NEEDS_APPROVAL",
                    "message": "confirm-class command",
                }
            ],
            "completion_state": "needs_consent",
            "stopped_reason": "needs_consent",
            "command_approval": {
                "command": "gh issue view 219",
                "reason": "confirm-class command",
            },
        }


class ExplicitModelApprovalTests(unittest.TestCase):
    """run_explicit_model normalizes the loop's approval signal (F17/F9)."""

    def _run(self, runner):
        from opaihub.ask import run_explicit_model

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            return run_explicit_model(
                root,
                "Fetch the issue",
                runner=runner,
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                allow_edits=True,
                tool_calling_enabled=True,
                record=False,
            )

    def test_command_approval_payload_is_normalized(self):
        result = self._run(_NeedsConsentLoopRunner())

        self.assertEqual(
            result["command_approval"],
            {"command": "gh issue view 219", "reason": "confirm-class command"},
        )
        self.assertEqual(result["completion_state"], "needs_consent")
        self.assertFalse(result_is_completed(result))

    def test_top_level_command_with_needs_consent_is_recognized(self):
        class TopLevelRunner(_NeedsConsentLoopRunner):
            def complete_with_tools(self, prompt, **kwargs):
                return {
                    "text": "approval needed",
                    "tool_trace": [],
                    "completion_state": "needs_consent",
                    "command": "gh issue view 219",
                    "reason": "confirm-class command",
                }

        result = self._run(TopLevelRunner())

        self.assertEqual(result["command_approval"]["command"], "gh issue view 219")

    def test_trace_error_code_is_a_fallback_shape(self):
        class TraceOnlyRunner(_NeedsConsentLoopRunner):
            def complete_with_tools(self, prompt, **kwargs):
                return {
                    "text": "approval needed",
                    "tool_trace": [
                        {
                            "tool": "run_command",
                            "ok": False,
                            "error_code": "COMMAND_NEEDS_APPROVAL",
                            "command": "gh issue view 219",
                            "message": "confirm-class command",
                        }
                    ],
                    "completion_state": "needs_consent",
                    "stopped_reason": "needs_consent",
                }

        result = self._run(TraceOnlyRunner())

        self.assertEqual(result["command_approval"]["command"], "gh issue view 219")
        self.assertEqual(result["command_approval"]["reason"], "confirm-class command")


class GuiApprovalReplyTests(unittest.TestCase):
    """gui_pipeline translates the approval stop into an actionable card."""

    def _free_result(self):
        return {
            "status": "answered_by_free_api",
            "answer": "I need approval to run that command.",
            "completion_state": "needs_consent",
            "stopped_reason": "needs_consent",
            "command_approval": {
                "command": "gh issue view 219",
                "reason": "confirm-class command",
            },
            "tool_trace": [
                {
                    "tool": "run_command",
                    "ok": False,
                    "error_code": "COMMAND_NEEDS_APPROVAL",
                    "message": "confirm-class command",
                }
            ],
        }

    def test_free_run_reports_needs_command_approval_with_command_and_reason(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask", return_value=self._free_result()
            ) as ask_mock:
                result = handle_gui_message(
                    root,
                    "Explain issue 219",
                    model_id="free:gemini:gemini-3.1-flash-lite",
                    mode="ask",
                    allow_cloud=True,
                )

        self.assertEqual(result["status"], "needs_command_approval")
        self.assertEqual(result["command"], "gh issue view 219")
        self.assertEqual(result["reason"], "confirm-class command")
        self.assertEqual(
            result["command_approval"],
            {"command": "gh issue view 219", "reason": "confirm-class command"},
        )
        self.assertIn("gh issue view 219", result["answer"])
        # Awaiting the user — not a completion, not a fake green state.
        self.assertEqual(result["workflow"]["phase"], "blocked")
        self.assertNotEqual(result.get("completion_note"), "no_changes")
        # The pipeline did pass tool authority down for the turn.
        self.assertTrue(ask_mock.call_args.kwargs["tool_calling_enabled"])

    def test_allow_command_kwarg_threads_to_free_ask(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask", return_value=self._free_result()
            ) as ask_mock:
                handle_gui_message(
                    root,
                    "Explain issue 219",
                    model_id="free:gemini:gemini-3.1-flash-lite",
                    mode="ask",
                    allow_cloud=True,
                    allow_command="gh issue view 219",
                )

        self.assertEqual(
            ask_mock.call_args.kwargs["allow_command"], "gh issue view 219"
        )

    def test_allowCommand_frontend_spelling_threads_verbatim(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask", return_value=self._free_result()
            ) as ask_mock:
                handle_gui_message(
                    root,
                    "Explain issue 219",
                    model_id="free:gemini:gemini-3.1-flash-lite",
                    mode="ask",
                    allow_cloud=True,
                    allowCommand="gh issue view 219 --json title,body",
                )

        self.assertEqual(
            ask_mock.call_args.kwargs["allow_command"],
            "gh issue view 219 --json title,body",
        )

    def test_no_grant_sends_no_allow_command(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask", return_value=self._free_result()
            ) as ask_mock:
                handle_gui_message(
                    root,
                    "Explain issue 219",
                    model_id="free:gemini:gemini-3.1-flash-lite",
                    mode="ask",
                    allow_cloud=True,
                )

        self.assertIsNone(ask_mock.call_args.kwargs["allow_command"])


class ProviderCliPushApprovalTests(unittest.TestCase):
    """Round 5 finding 1+2: a push the hook refused becomes an approval card.

    A provider CLI runs git in its own shell, so its blocked push is refused by
    the out-of-process PreToolUse hook, which cannot put anything into the run
    result. It records the refusal through ``opaihub.command_consent`` instead.
    Without reading that back, the turn ended on the model's own prose — which in
    the live session claimed the branch "has been successfully pushed" while the
    status pill read Failed. These pin both halves of the fix.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"OPAI_COMMAND_CONSENT_DIR": self._tmp.name}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, root, answer, *, refuses=None, **kwargs):
        """One turn whose provider optionally hits the hook's push gate mid-run.

        ``refuses`` is the command the out-of-process hook denied, recorded from
        inside the provider call — the only point at which it can happen, since
        the pipeline clears stale refusals before the run starts.
        """
        from opaihub import command_consent
        from opaihub.gui_pipeline import handle_gui_message

        def _provider(*_args, **_kwargs):
            if refuses:
                command_consent.record_pending(
                    refuses, "Pushing sends this branch to the remote."
                )
            return {
                "status": "answered_by_account",
                "answer": answer,
                "completion_state": "completed",
                "stopped_reason": "",
                "tool_trace": [],
            }

        with mock.patch("opai.app_state.ask", side_effect=_provider):
            return handle_gui_message(
                root,
                "Push the current branch",
                model_id="account:claude:sonnet",
                mode="full-auto",
                **kwargs,
            )

    def test_a_hook_refusal_becomes_an_approval_card_not_a_success_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = self._run(
                root,
                "The current branch has been successfully pushed to the origin remote.",
                refuses="git push origin main",
            )

        self.assertEqual(result["status"], "needs_command_approval")
        self.assertEqual(result["command"], "git push origin main")
        self.assertIn("git push origin main", result["answer"])
        # The model's contradicting success claim is not what the user is shown.
        self.assertNotIn("successfully pushed", result["answer"])

    def test_pr_comment_refusal_cannot_complete_from_an_unrelated_local_change(self):
        """A blocked GitHub comment remains blocked even if the run wrote a file."""

        command = "gh pr comment 511 --repo MarcoLadeira/OPai --body-file .comment.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = self._run(
                root,
                "I saved the comment locally, so the pull request is updated.",
                refuses=command,
            )

        self.assertEqual(result["status"], "needs_command_approval")
        self.assertEqual(result["command"], command)
        self.assertEqual(result["completion_verdict"]["verdict"], "blocked")
        self.assertNotIn("pull request is updated", result["answer"])

    def test_a_grant_is_armed_where_the_hook_can_read_it(self):
        from opaihub import command_consent

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            armed = {}

            def _capture(*_args, **_kwargs):
                # Sampled while the provider "runs", which is when the hook reads it.
                armed["command"] = command_consent.granted_command()
                return {
                    "status": "answered_by_account",
                    "answer": "Pushed.",
                    "completion_state": "completed",
                    "stopped_reason": "",
                    "tool_trace": [],
                }

            from opaihub.gui_pipeline import handle_gui_message

            with mock.patch("opai.app_state.ask", side_effect=_capture):
                handle_gui_message(
                    root,
                    "Push the current branch",
                    model_id="account:claude:sonnet",
                    mode="full-auto",
                    allowCommand="git push origin main",
                )

        self.assertEqual(armed["command"], "git push origin main")
        # And it does not outlive the turn.
        self.assertEqual(command_consent.granted_command(), "")

    def test_a_refusal_from_a_previous_turn_is_not_resurfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            first = self._run(root, "Pushed.", refuses="git push")
            self.assertEqual(first["status"], "needs_command_approval")
            # The next turn asks nothing of the gate, so it must not inherit the
            # previous turn's approval card.
            second = self._run(root, "Here is the branch state.")

        self.assertNotEqual(second["status"], "needs_command_approval")

    def test_success_prose_under_a_non_completed_verdict_is_marked_unverified(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask",
                return_value={
                    "status": "answered_by_account",
                    "answer": (
                        "The current branch has been successfully pushed to the "
                        "origin remote."
                    ),
                    "completion_state": "failed",
                    "stopped_reason": "provider_failed",
                    "tool_trace": [],
                },
            ):
                result = handle_gui_message(
                    root,
                    "Push the current branch",
                    model_id="account:claude:sonnet",
                    mode="full-auto",
                )

        verdict = result["completion_verdict"]
        self.assertNotEqual(verdict["verdict"], "completed")
        # The flag the renderer reads: pill and prose can no longer disagree
        # silently, because the claim is labelled unverified where it is written.
        self.assertTrue(verdict["answer_conflicts"])

    def test_a_verified_run_carries_no_conflict_flag(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opai.app_state.ask",
                return_value={
                    "status": "answered_by_account",
                    "answer": "The branch has been successfully pushed to origin.",
                    "completion_state": "completed",
                    "stopped_reason": "",
                    "tool_trace": [],
                },
            ):
                result = handle_gui_message(
                    root,
                    "What does app.py do?",
                    model_id="account:claude:sonnet",
                    mode="ask",
                )

        self.assertEqual(result["completion_verdict"]["verdict"], "completed")
        self.assertFalse(result["completion_verdict"]["answer_conflicts"])


class AccountCompletionTruthTests(unittest.TestCase):
    """F24: _ask_account emits canonical completion truth from runner signals."""

    def _ask(self, runner):
        from opai.app_state import ask

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            return ask(
                root,
                "task",
                model_choice="account:claude:sonnet",
                account_runner=runner,
            )

    def test_success_maps_to_completed(self):
        result = self._ask(FakeAccountRunner(text="A real answer.", cost=0.01))

        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["completion_state"], "completed")
        self.assertEqual(result["stopped_reason"], "")
        self.assertTrue(result_is_completed(result))

    def test_is_error_subtype_is_not_completed(self):
        class ErrorRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                return {
                    "text": "partial work",
                    "cost": 0.01,
                    "is_error": True,
                    "subtype": "error_during_execution",
                }

        result = self._ask(ErrorRunner())

        self.assertEqual(result["completion_state"], "failed")
        self.assertEqual(result["stopped_reason"], "error_during_execution")
        self.assertFalse(result_is_completed(result))

    def test_max_turns_subtype_is_not_completed(self):
        class MaxTurnsRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                return {
                    "text": "ran out of turns",
                    "cost": 0.02,
                    "subtype": "error_max_turns",
                }

        result = self._ask(MaxTurnsRunner())

        self.assertEqual(result["completion_state"], "failed")
        self.assertEqual(result["stopped_reason"], "error_max_turns")
        self.assertFalse(result_is_completed(result))

    def test_permission_denied_maps_to_needs_consent(self):
        class DeniedRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                return {
                    "text": "I could not run the command.",
                    "cost": 0.01,
                    "permission_denials": [
                        {"tool": "Bash", "command": "gh issue view 219"}
                    ],
                }

        result = self._ask(DeniedRunner())

        self.assertEqual(result["completion_state"], "needs_consent")
        self.assertEqual(result["stopped_reason"], "approval_required")
        self.assertFalse(result_is_completed(result))
        self.assertIs(
            completion_state_from_legacy(result), CompletionState.NEEDS_CONSENT
        )

    def test_streaming_permission_denied_maps_to_needs_consent(self):
        class DeniedStreamingRunner(FakeStreamingRunner):
            def stream(self, prompt, **kwargs):
                return {
                    "text": "blocked on a permission",
                    "cost": 0.01,
                    "permission_denied": True,
                }

        from opai.app_state import ask

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = ask(
                root,
                "task",
                model_choice="account:claude:opus",
                account_runner=DeniedStreamingRunner(),
                on_text=lambda chunk: None,
            )

        self.assertEqual(result["completion_state"], "needs_consent")
        self.assertFalse(result_is_completed(result))

    def test_explicit_runner_completion_state_wins(self):
        class ExplicitRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                return {
                    "text": "some prose",
                    "cost": 0.01,
                    "completion_state": "stuck_no_progress",
                    "stopped_reason": "no_progress",
                }

        result = self._ask(ExplicitRunner())

        self.assertEqual(result["completion_state"], "stuck_no_progress")
        self.assertFalse(result_is_completed(result))


class EditIntentHonestyTests(unittest.TestCase):
    """F14/F24: edit-intent runs with zero change evidence are not green."""

    def test_implement_run_with_zero_changes_is_not_green_completed(self):
        from opaihub.gui_pipeline import handle_gui_message

        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = handle_gui_message(
                root,
                "Fix the bug in app.py",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=FakeAccountRunner(
                    text="Done! I've successfully fixed the bug."
                ),
                on_event=events.append,
            )

        titles = [str(e.get("title") or "") for e in events]
        self.assertNotIn("OPai completed", titles)
        self.assertIn("OPai finished with no changes", titles)
        completed_events = [e for e in events if e["type"] == "completed"]
        self.assertEqual(completed_events[-1]["status"], "warning")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["completion_note"], "no_changes")
        self.assertEqual(result["workflow"]["phase"], "completed")
        self.assertIn("no changes", result["workflow"]["message"])
        # tests_status still surfaces honestly.
        self.assertEqual(result["workflow"]["tests_status"], "not_verified")

    def test_implement_run_with_real_changes_stays_green(self):
        from opaihub.gui_pipeline import handle_gui_message

        class EditingRunner(FakeAccountRunner):
            def __init__(self, target: Path):
                super().__init__(text="Fixed it.")
                self._target = target

            def complete(self, prompt, **kwargs):
                self._target.write_text("value = 2\n", encoding="utf-8")
                return super().complete(prompt, **kwargs)

        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = handle_gui_message(
                root,
                "Fix the bug in app.py",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=EditingRunner(root / "app.py"),
                on_event=events.append,
            )

        # #378: the green marker is the evidence-backed completion verdict.
        verdicts = [e for e in events if e.get("type") == "completion_verdict"]
        self.assertTrue(verdicts, [e.get("type") for e in events])
        self.assertEqual(verdicts[-1]["status"], "success")
        self.assertEqual(result["completion_note"], "")
        self.assertEqual(result["workflow"]["phase"], "reviewing_diff")
        self.assertTrue(result["changed_files"])

    def test_read_only_run_stays_green_on_a_real_answer(self):
        from opaihub.gui_pipeline import handle_gui_message

        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = handle_gui_message(
                root,
                "Explain how routing works.",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="Here is how it works."),
                on_event=events.append,
            )

        # #378: the green marker is the evidence-backed completion verdict.
        verdicts = [e for e in events if e.get("type") == "completion_verdict"]
        self.assertTrue(verdicts, [e.get("type") for e in events])
        self.assertEqual(verdicts[-1]["status"], "success")
        self.assertEqual(result["completion_note"], "")
        self.assertEqual(result["workflow"]["phase"], "completed")

    def test_free_implement_run_with_zero_changes_is_not_green(self):
        from opaihub.gui_pipeline import handle_gui_message

        fake_result = {
            "status": "answered_by_free_api",
            "answer": "Done! I've successfully solved GitHub issue #219.",
            "source": "free_api",
            "model_id": "free:gemini:gemini-3.1-flash-lite",
            "completion_state": "completed",
            "tool_trace": [],
            "changed_files": [],
        }
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("opai.app_state.ask", return_value=fake_result):
                result = handle_gui_message(
                    root,
                    "Solve GitHub issue #219 in this repo for me.",
                    model_id="free:gemini:gemini-3.1-flash-lite",
                    mode="safe-auto",
                    allow_cloud=True,
                    on_event=events.append,
                )

        titles = [str(e.get("title") or "") for e in events]
        self.assertNotIn("OPai completed", titles)
        self.assertIn("OPai finished with no changes", titles)
        self.assertEqual(result["completion_note"], "no_changes")


if __name__ == "__main__":
    unittest.main()
