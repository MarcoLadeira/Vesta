"""F26 edit-approval flow + F27 no-progress guard contracts.

QA pass-2 (docs/QA_E2E_ISSUE219_PASS2_2026-07-18.md) left two gaps on the
account path:

- **F26** — in Safe Auto the provider CLI refuses Edit/Write non-interactively
  ("you haven't granted it yet") and the run ended in a prose dead end. The
  contract now: the stream collects the denied file paths; the pipeline
  returns ``status == "needs_edit_approval"`` with the exact files; the
  ``allow_edits_once``/``allowEditsOnce`` grant threads down to the runner as
  ``edit_grant`` which maps to the claude CLI's ``--permission-mode
  acceptEdits`` — in Safe Auto only, never widening read-only modes.
- **F27** — an edit-intent run that keeps exploring without one edit attempt
  is checkpointed by the no-progress guard and can never render green.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from _helpers import FakeStreamingRunner, make_repo

from opaihub.completion import result_is_completed


@pytest.fixture(autouse=True)
def _clean_broken_git_config_env():
    """Scrub inherited GIT_CONFIG_* so repo helpers stay hermetic."""
    for name in list(os.environ):
        if name == "GIT_TERMINAL_PROMPT" or name.startswith("GIT_CONFIG_"):
            os.environ.pop(name, None)
    yield


def _claude_line(payload: dict) -> str:
    return json.dumps(payload)


def _tool_use_line(tool: str, tool_id: str, **input_fields) -> str:
    return _claude_line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool,
                        "input": input_fields,
                    }
                ]
            },
        }
    )


def _tool_result_line(tool_id: str, text: str, *, is_error: bool = True) -> str:
    return _claude_line(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": is_error,
                        "content": text,
                    }
                ]
            },
        }
    )


_DENIAL_TEXT = (
    "Claude requested permissions to write to {path}, but you haven't granted it yet."
)


class ActivityEditDenialTests(unittest.TestCase):
    """The stream session records which files were permission-refused."""

    def _session(self):
        from opai.activity import ActivitySession

        return ActivitySession()

    def test_denied_write_records_the_file_path(self):
        session = self._session()
        session.parse_claude_line(
            _tool_use_line("Write", "toolu_1", file_path="C:\\repo\\app.py")
        )
        session.parse_claude_line(
            _tool_result_line("toolu_1", _DENIAL_TEXT.format(path="C:\\repo\\app.py"))
        )

        self.assertEqual(session.edit_denials, ["C:\\repo\\app.py"])

    def test_denied_edit_and_write_dedupe_and_preserve_order(self):
        session = self._session()
        for seq, (tool, path) in enumerate(
            [("Edit", "a.py"), ("Write", "b.py"), ("Edit", "a.py")]
        ):
            tool_id = f"toolu_{seq}"
            session.parse_claude_line(_tool_use_line(tool, tool_id, file_path=path))
            session.parse_claude_line(
                _tool_result_line(tool_id, _DENIAL_TEXT.format(path=path))
            )

        self.assertEqual(session.edit_denials, ["a.py", "b.py"])

    def test_ordinary_edit_error_is_not_a_denial(self):
        session = self._session()
        session.parse_claude_line(_tool_use_line("Edit", "toolu_1", file_path="a.py"))
        session.parse_claude_line(
            _tool_result_line("toolu_1", "String to replace not found in file.")
        )

        self.assertEqual(session.edit_denials, [])

    def test_denied_bash_command_is_not_an_edit_denial(self):
        session = self._session()
        session.parse_claude_line(
            _tool_use_line("Bash", "toolu_1", command="gh issue view 219")
        )
        session.parse_claude_line(
            _tool_result_line(
                "toolu_1",
                "This command requires approval: you haven't granted it yet.",
            )
        )

        self.assertEqual(session.edit_denials, [])


class CommandLengthErrorTests(unittest.TestCase):
    """F25: the CLI's ~965-byte parser limit renders as actionable guidance."""

    def _session(self):
        from opai.activity import ActivitySession

        return ActivitySession()

    def test_too_long_error_becomes_guidance(self):
        session = self._session()
        session.parse_claude_line(
            _tool_use_line("Bash", "toolu_1", command="powershell -Command ...")
        )
        events = session.parse_claude_line(
            _tool_result_line(
                "toolu_1",
                "Command contains malformed syntax that cannot be parsed: "
                "Command too long for parsing (1391 bytes). Maximum supported "
                "length is 965 bytes.",
            )
        )["events"]

        detail = str(events[-1].get("detail") or "")
        self.assertIn("parser limit", detail)
        self.assertIn("Edit/Write", detail)
        self.assertNotIn("965 bytes.", detail)  # raw diagnostic is replaced

    def test_ordinary_command_error_is_unchanged(self):
        from opai.activity import _friendly_tool_error

        self.assertIsNone(_friendly_tool_error("command not found: gh"))


class BuildCommandGrantTests(unittest.TestCase):
    """edit_grant maps to acceptEdits in Safe Auto only (F26)."""

    def _runner(self):
        from opaihub.accounts import AccountRunner

        return AccountRunner("claude", "claude")

    def test_safe_auto_with_grant_adds_accept_edits(self):
        cmd = self._runner().build_command(
            "task", allow_edits=True, mode="safe-auto", edit_grant=True
        )

        self.assertIn("--permission-mode", cmd)
        self.assertIn("acceptEdits", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_safe_auto_without_grant_stays_gated(self):
        cmd = self._runner().build_command("task", allow_edits=True, mode="safe-auto")

        self.assertNotIn("--permission-mode", cmd)
        self.assertNotIn("acceptEdits", cmd)

    def test_full_auto_ignores_the_grant(self):
        cmd = self._runner().build_command(
            "task", allow_edits=True, mode="full-auto", edit_grant=True
        )

        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertNotIn("acceptEdits", cmd)

    def test_read_only_modes_never_get_accept_edits(self):
        # Not even with a grant: Ask and Plan have no approval path to grant.
        for mode in ("ask", "plan"):
            with self.subTest(mode=mode):
                cmd = self._runner().build_command(
                    "task", allow_edits=False, mode=mode, edit_grant=True
                )
                self.assertNotIn("acceptEdits", cmd)

    def test_manual_applies_an_edit_the_user_granted(self):
        # 40d6dc0: Manual "asks, and a granted edit applies". It used to be
        # listed as read-only here, which asserted the opposite.
        cmd = self._runner().build_command(
            "task", allow_edits=True, mode="approve-edits", edit_grant=True
        )

        self.assertIn("acceptEdits", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_manual_without_a_grant_still_asks(self):
        cmd = self._runner().build_command(
            "task", allow_edits=True, mode="approve-edits"
        )

        self.assertNotIn("acceptEdits", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)


class _DeniedEditStreamRunner(FakeStreamingRunner):
    """Stream result carrying the collected edit denials (no CLI launched)."""

    def __init__(self, *, denials, text="I could not edit the files."):
        super().__init__()
        self._denials = list(denials)
        self._text = text
        self.stream_calls: list[dict] = []

    def stream(self, prompt, *, edit_grant=False, **kwargs):
        self.stream_calls.append({"prompt": prompt, "edit_grant": edit_grant})
        return {
            "text": self._text,
            "cost": 0.01,
            "edit_denials": list(self._denials),
        }


class PipelineEditApprovalTests(unittest.TestCase):
    """The pipeline turns edit denials into an actionable card (F26)."""

    def _run(self, runner, **kwargs):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            return handle_gui_message(
                root,
                "Fix the bug in app.py",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=runner,
                on_text=lambda chunk: None,
                **kwargs,
            )

    def test_safe_auto_denials_return_needs_edit_approval(self):
        result = self._run(_DeniedEditStreamRunner(denials=["app.py", "b.py"]))

        self.assertEqual(result["status"], "needs_edit_approval")
        self.assertEqual(result["edit_files"], ["app.py", "b.py"])
        self.assertEqual(result["edit_approval"], {"files": ["app.py", "b.py"]})
        self.assertIn("app.py", result["answer"])
        # Awaiting the user — never a completion.
        self.assertEqual(result["workflow"]["phase"], "blocked")

    def test_grant_threads_to_runner_and_skips_the_card(self):
        runner = _DeniedEditStreamRunner(denials=["app.py"])
        result = self._run(runner, allow_edits_once=True)

        self.assertTrue(runner.stream_calls[0]["edit_grant"])
        self.assertNotEqual(result["status"], "needs_edit_approval")

    def test_frontend_spelling_allowEditsOnce_threads(self):
        runner = _DeniedEditStreamRunner(denials=["app.py"])
        self._run(runner, allowEditsOnce=True)

        self.assertTrue(runner.stream_calls[0]["edit_grant"])

    def test_no_denials_no_card(self):
        runner = _DeniedEditStreamRunner(denials=[], text="Fixed it.")
        result = self._run(runner)

        self.assertNotEqual(result["status"], "needs_edit_approval")


class _NoProgressStreamRunner(FakeStreamingRunner):
    def stream(self, prompt, **kwargs):
        return {
            "text": "",
            "cost": 0.02,
            "no_progress": True,
            "stopped_reason": "no_progress_guard",
            "tool_steps": 61,
        }


class NoProgressGuardTests(unittest.TestCase):
    """F27: a guard-checkpointed run is honest — never green, never empty."""

    def test_guard_result_maps_to_stuck_no_progress(self):
        from opai.app_state import ask

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = ask(
                root,
                "Fix the bug in app.py",
                model_choice="account:claude:sonnet",
                account_runner=_NoProgressStreamRunner(),
                on_text=lambda chunk: None,
            )

        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["completion_state"], "stuck_no_progress")
        self.assertEqual(result["stopped_reason"], "no_progress_guard")
        self.assertFalse(result_is_completed(result))
        self.assertIn("61", result["answer"])
        # #648: the guard was renamed when it stopped counting edits and started
        # scoring evidence. The property this test protects is unchanged -- the
        # message must name the mechanism that stopped the run, so the user
        # knows it was a deliberate guard and not a crash.
        self.assertIn("convergence guard", result["answer"])
        # And it must no longer assert a cause it cannot know. This fixture
        # reports no trigger, so the text has to stay general rather than
        # blaming a missing edit.
        self.assertNotIn("without a single edit attempt", result["answer"])

    def test_guard_env_knobs_parse_defensively(self):
        from opaihub.accounts import _guard_int_env

        with mock.patch.dict(os.environ, {"OPAI_NO_PROGRESS_STEP_BUDGET": "25"}):
            self.assertEqual(_guard_int_env("OPAI_NO_PROGRESS_STEP_BUDGET", 60), 25)
        with mock.patch.dict(os.environ, {"OPAI_NO_PROGRESS_STEP_BUDGET": "0"}):
            self.assertEqual(_guard_int_env("OPAI_NO_PROGRESS_STEP_BUDGET", 60), 0)
        for bad in ("nope", "-5", ""):
            with mock.patch.dict(os.environ, {"OPAI_NO_PROGRESS_STEP_BUDGET": bad}):
                self.assertEqual(_guard_int_env("OPAI_NO_PROGRESS_STEP_BUDGET", 60), 60)


if __name__ == "__main__":
    unittest.main()
