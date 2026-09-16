"""Manual and Accept Edits can edit, as the mode contract says they do.

40d6dc0 wrote the contract and fixed the provider command lines to match:

  plan / ask      edits block
  approve-edits   edits ask      Manual: "it asks, and a granted edit applies"
  safe-auto       edits ask      Auto
  auto-edits      edits allow    Accept Edits
  full-auto       edits allow    Bypass Permissions

But the pipeline still decided "will this turn edit?" from ``{safe-auto,
full-auto}``. So a Manual or Accept Edits turn that asked for a fix ran
read-only whatever the command line said, and Manual had no way to ask: the
CLI refused the edit and the run simply ended. Ask mode, meanwhile, is upgraded
to Auto by an explicit "fix X" -- so Manual, which is meant to sit *above* Ask,
could do less than it.

The other half matters as much: nothing here may hand out authority the user
did not give. Vesta's own tool loop (free and local models) cannot stop at an
edit and ask, so in Manual it edits only with the user's one-shot grant.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from _helpers import FakeStreamingRunner, make_repo

from vestahub.gui_pipeline import handle_gui_message, request_tool_authority

FIX = "Fix the bug in app.py"
FREE_MODEL = "free:gemini:gemini-3.1-flash-lite"


class _RecordingStreamRunner(FakeStreamingRunner):
    """An account runner that records what it was allowed to do."""

    def __init__(self, *, denials: tuple[str, ...] = (), text: str = "Done.") -> None:
        super().__init__()
        self._denials = list(denials)
        self._text = text
        self.stream_calls: list[dict[str, Any]] = []

    def stream(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.stream_calls.append(dict(kwargs))
        return {"text": self._text, "cost": 0.01, "edit_denials": list(self._denials)}


class _RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(
            Path(self._tmp.name), files={"app.py": "value = 1\n"}, commit=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _account_turn(
        self, mode: str, runner: _RecordingStreamRunner, message: str = FIX, **kw: Any
    ) -> dict[str, Any]:
        return handle_gui_message(
            self.root,
            message,
            model_id="account:claude:sonnet",
            mode=mode,
            account_runner=runner,
            on_text=lambda _chunk: None,
            **kw,
        )

    def _free_call(self, mode: str, **kw: Any) -> Any:
        """The call the pipeline made into the free-model tool loop."""

        answer = {
            "status": "answered_by_free_api",
            "answer": "Free-tier answer",
            "source": "free_api",
            "model_id": FREE_MODEL,
        }
        with mock.patch("vesta.app_state.ask", return_value=answer) as ask:
            handle_gui_message(
                self.root, FIX, model_id=FREE_MODEL, mode=mode, allow_cloud=True, **kw
            )
        return ask.call_args

    def _free_turn(self, mode: str, **kw: Any) -> dict[str, Any]:
        return self._free_call(mode, **kw).kwargs

    def _local_turn(self, mode: str, **kw: Any) -> dict[str, Any]:
        """What the pipeline let a local model's tool loop do."""

        answer = {"status": "answered_locally", "answer": "Local answer"}
        with (
            mock.patch("vestahub.ask.run_ask", return_value=answer) as run_ask,
            mock.patch("vestahub.local_runner.runner_for_model", return_value=None),
        ):
            handle_gui_message(
                self.root, FIX, model_id="ollama:qwen2.5-coder", mode=mode, **kw
            )
        self.assertTrue(run_ask.called, "the turn never reached the local path")
        return run_ask.call_args.kwargs


class AccountPathTests(_RepoCase):
    """The account CLIs can ask, so every edit-capable mode may edit there."""

    def test_every_edit_capable_mode_lets_a_fix_edit(self) -> None:
        for mode in ("approve-edits", "safe-auto", "auto-edits", "full-auto"):
            with self.subTest(mode=mode):
                runner = _RecordingStreamRunner()
                self._account_turn(mode, runner)
                call = runner.stream_calls[-1]
                self.assertTrue(call["allow_edits"], mode)
                # The mode the user picked is the mode that ran.
                self.assertEqual(call["mode"], mode)

    def test_manual_asks_with_the_card_when_the_cli_refuses_an_edit(self) -> None:
        # Before: the CLI refused, no card was offered, the run just ended --
        # a mode whose panel says "edit: ask" could never edit.
        result = self._account_turn(
            "approve-edits", _RecordingStreamRunner(denials=("app.py",))
        )

        self.assertEqual(result["status"], "needs_edit_approval")
        self.assertEqual(result["edit_files"], ["app.py"])
        self.assertIn("app.py", result["answer"])

    def test_manual_applies_the_edit_once_the_user_grants_it(self) -> None:
        runner = _RecordingStreamRunner(denials=("app.py",))
        result = self._account_turn("approve-edits", runner, allow_edits_once=True)

        self.assertTrue(runner.stream_calls[-1].get("edit_grant"))
        self.assertNotEqual(result["status"], "needs_edit_approval")

    def test_manual_without_a_grant_does_not_carry_one(self) -> None:
        runner = _RecordingStreamRunner()
        self._account_turn("approve-edits", runner)

        self.assertFalse(runner.stream_calls[-1].get("edit_grant", False))

    def test_accept_edits_never_shows_an_edit_card(self) -> None:
        # Accept Edits applies edits without asking; a refusal there is the
        # provider's, not a question for the user.
        result = self._account_turn(
            "auto-edits", _RecordingStreamRunner(denials=("app.py",))
        )

        self.assertNotEqual(result["status"], "needs_edit_approval")

    def test_a_question_stays_read_only_in_every_mode(self) -> None:
        # Widening the modes must not turn an explanation into an edit.
        for mode in ("approve-edits", "auto-edits"):
            with self.subTest(mode=mode):
                runner = _RecordingStreamRunner(text="It adds two numbers.")
                self._account_turn(mode, runner, message="summarize my changes")
                self.assertFalse(runner.stream_calls[-1]["allow_edits"])

    def test_a_discovery_request_stays_read_only(self) -> None:
        for mode in ("approve-edits", "auto-edits"):
            with self.subTest(mode=mode):
                runner = _RecordingStreamRunner(text="Issue #4 looks good.")
                self._account_turn(mode, runner, message="find me a git issue to solve")
                self.assertFalse(runner.stream_calls[-1]["allow_edits"])


class ToolLoopTests(_RepoCase):
    """Vesta's own loop cannot ask mid-run, so Manual needs the grant there."""

    def test_manual_does_not_edit_on_a_free_model_without_a_grant(self) -> None:
        self.assertFalse(self._free_turn("approve-edits")["allow_edits"])

    def test_manual_edits_on_a_free_model_once_granted(self) -> None:
        kwargs = self._free_turn("approve-edits", allow_edits_once=True)
        self.assertTrue(kwargs["allow_edits"])

    def test_accept_edits_edits_on_a_free_model(self) -> None:
        # Before: read-only, so Accept Edits on a free model could not edit.
        self.assertTrue(self._free_turn("auto-edits")["allow_edits"])

    def test_a_local_model_follows_the_same_rule(self) -> None:
        # Local models run the same loop, reached down a different branch.
        self.assertFalse(self._local_turn("approve-edits")["allow_edits"])
        self.assertTrue(
            self._local_turn("approve-edits", allow_edits_once=True)["allow_edits"]
        )
        self.assertTrue(self._local_turn("auto-edits")["allow_edits"])

    def test_auto_and_bypass_are_unchanged_on_a_free_model(self) -> None:
        for mode in ("safe-auto", "full-auto"):
            with self.subTest(mode=mode):
                self.assertTrue(self._free_turn(mode)["allow_edits"])

    def test_the_prompt_names_only_the_tools_the_loop_really_has(self) -> None:
        # The prompt lists the loop's real tools. Manual without a grant must
        # not be told it can write_file when the loop will not offer it -- and
        # the granted turn must be, or this assertion proves nothing.
        ungranted = str(self._free_call("approve-edits").args[1])
        granted = str(self._free_call("approve-edits", allow_edits_once=True).args[1])

        self.assertNotIn("write_file", ungranted)
        self.assertIn("write_file", granted)


class ToolAuthorityTests(unittest.TestCase):
    """``request_tool_authority`` is the seam the permission panel is held to."""

    def _allows(self, mode: str, *, grant: bool = False, message: str = FIX) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            return request_tool_authority(
                message, selected_mode=mode, repo_root=root, edit_grant=grant
            ).allow_edits

    def test_manual_is_read_only_until_granted(self) -> None:
        self.assertFalse(self._allows("approve-edits"))
        self.assertTrue(self._allows("approve-edits", grant=True))

    def test_accept_edits_may_edit(self) -> None:
        self.assertTrue(self._allows("auto-edits"))

    def test_a_grant_never_opens_a_read_only_mode(self) -> None:
        for mode in ("ask", "plan"):
            with self.subTest(mode=mode):
                self.assertFalse(self._allows(mode, grant=True))

    def test_a_grant_never_turns_discovery_into_an_edit(self) -> None:
        self.assertFalse(
            self._allows(
                "approve-edits", grant=True, message="find me a git issue to solve"
            )
        )

    def test_the_default_is_no_grant(self) -> None:
        # A caller that predates the grant gets the conservative answer.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            authority = request_tool_authority(
                FIX, selected_mode="approve-edits", repo_root=root
            )
        self.assertFalse(authority.allow_edits)


if __name__ == "__main__":
    unittest.main()
