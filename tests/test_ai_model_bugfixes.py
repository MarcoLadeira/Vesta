"""Regressions for the "talking to AI models is broken" bug batch.

Covers: no flashing console windows on Windows (subprocess CREATE_NO_WINDOW),
free-tier/local chat not being polluted with test-run instructions, the Codex
invalid-config error surfacing a repair path, and a streamed answer surviving a
non-zero CLI exit. No real CLI, model, or network is used.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub import proc


# ---------------------------------------------------------------------------
# Bug: tons of terminals flash on every message (missing CREATE_NO_WINDOW).
# ---------------------------------------------------------------------------
class NoWindowKwargsTests(unittest.TestCase):
    def test_matches_current_platform(self):
        result = proc.no_window_kwargs()
        if sys.platform == "win32":
            self.assertEqual(result, {"creationflags": subprocess.CREATE_NO_WINDOW})
        else:
            self.assertEqual(result, {})

    def _assert_passes_no_window(self, module, run_callable):
        """run_callable triggers a subprocess.run in `module`; assert flags flow."""
        expected = proc.no_window_kwargs()
        captured: dict = {}

        class _Done:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            return _Done()

        with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
            run_callable()
        for key, value in expected.items():
            self.assertEqual(
                captured.get(key), value, f"{module.__name__} dropped {key}"
            )

    def test_command_runner_passes_no_window(self):
        import opaihub.command_runner as cr

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._assert_passes_no_window(
                cr, lambda: cr.run_policy_command(["git", "status"], root, timeout=5)
            )

    def test_evidence_cache_git_passes_no_window(self):
        import opaihub.evidence_cache as ec

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            if not ec.shutil.which("git"):
                self.skipTest("git not available")
            self._assert_passes_no_window(
                ec, lambda: ec._git_output(root, ["rev-parse", "HEAD"])
            )

    def test_test_select_git_passes_no_window(self):
        import opaihub.test_select as ts

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            if not ts.shutil.which("git"):
                self.skipTest("git not available")
            self._assert_passes_no_window(ts, lambda: ts._git_changed_files(root))


# ---------------------------------------------------------------------------
# Bug: "hello" to a free/local model replies with how to run the project tests.
# ---------------------------------------------------------------------------
class ChatPromptFramingTests(unittest.TestCase):
    def test_system_prompt_guards_against_project_talk_on_greetings(self):
        from opaihub.ask import SYSTEM_PROMPT

        low = SYSTEM_PROMPT.lower()
        self.assertIn("greeting", low)
        self.assertIn("relevant", low)
        # It must tell the model NOT to volunteer tests/files for small talk.
        self.assertTrue("test" in low and "not" in low)

    def test_build_prompt_leads_with_user_message_and_optional_context(self):
        from opaihub.ask import _build_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            built = _build_prompt(root, "hello")
        self.assertTrue(built.startswith("User message: hello"))
        low = built.lower()
        self.assertIn("only if it is relevant", low)
        # Context is still available for real coding tasks, just not forced.
        self.assertIn("project context", low)

    def test_capturing_runner_receives_reframed_system_and_prompt(self):
        from opaihub.ask import SYSTEM_PROMPT, run_ask

        seen: dict = {}

        class CapturingRunner:
            name = "capture"
            model = "fake"

            def available(self):
                return True

            def complete(self, prompt, *, system=None, timeout=60.0):
                seen["prompt"] = prompt
                seen["system"] = system
                return "Hi! How can I help?"

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = run_ask(root, "hello", runner=CapturingRunner(), record=False)
        self.assertEqual(result["status"], "answered_locally")
        self.assertEqual(seen["system"], SYSTEM_PROMPT)
        self.assertTrue(seen["prompt"].startswith("User message: hello"))


# ---------------------------------------------------------------------------
# Bug: Codex "unknown variant `default`" surfaced as a generic failure.
# ---------------------------------------------------------------------------
class CodexConfigErrorTests(unittest.TestCase):
    def test_unknown_variant_is_config_invalid(self):
        from opai.provider_contract import classify_error_code

        detail = (
            "Error loading configuration: C:\\Users\\x\\.codex\\config.toml:6:16: "
            "unknown variant `default`, expected `fast` or `flex`"
        )
        self.assertEqual(classify_error_code(detail, returncode=1), "CONFIG_INVALID")

    def test_config_invalid_message_points_to_repair(self):
        from opai.provider_contract import normalize_provider_error

        detail = (
            "Error loading configuration: unknown variant `default`, expected `fast`"
        )
        err = normalize_provider_error("codex", detail, returncode=1)
        self.assertEqual(err["code"], "CONFIG_INVALID")
        self.assertIn("repair", err["userMessage"].lower())
        self.assertIn("open_settings", err["recoveryActions"])
        self.assertFalse(err["retryable"])

    def test_normal_errors_still_classify_normally(self):
        from opai.provider_contract import classify_error_code

        self.assertEqual(
            classify_error_code("401 Invalid authentication credentials"),
            "AUTH_INVALID",
        )

    def test_config_invalid_offers_repair_action(self):
        from opai.provider_contract import normalize_provider_error

        err = normalize_provider_error(
            "codex", "unknown variant `default`, expected `fast`", returncode=1
        )
        self.assertIn("repair_config", err["recoveryActions"])

    def _codex_config_error(self):
        from opai.provider_contract import normalize_provider_error

        return normalize_provider_error(
            "codex",
            "Error loading configuration: unknown variant `default`, expected `fast`",
            returncode=1,
        )

    def test_connection_surfaces_config_error_not_cli_unavailable(self):
        # The misconfigured diagnostic must reflect the real config problem and
        # carry the structured error, not the misleading "CLI unavailable".
        from opaihub.accounts import connection_for_account

        conn = connection_for_account(
            {
                "id": "codex",
                "label": "Codex",
                "cli_present": True,
                "authenticated": True,
            },
            auth_status="misconfigured",
            error=self._codex_config_error(),
        )
        self.assertEqual(conn["lastErrorCode"], "CONFIG_INVALID")
        self.assertEqual(conn["error"]["code"], "CONFIG_INVALID")
        self.assertNotIn("unavailable", conn["safeDiagnostic"].lower())
        self.assertIn("invalid setting", conn["safeDiagnostic"].lower())

    def test_pipeline_preserves_config_error_and_repair_guidance(self):
        # A codex send that hits the config error must return the CONFIG_INVALID
        # message + repair action, not a generic "could not complete" card.
        from opaihub.gui_pipeline import handle_gui_message
        from opaihub.accounts import connection_for_account

        conn = connection_for_account(
            {
                "id": "codex",
                "label": "Codex",
                "cli_present": True,
                "authenticated": True,
            },
            auth_status="misconfigured",
            error=self._codex_config_error(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.accounts.test_account_connection", return_value=conn
            ):
                result = handle_gui_message(
                    root, "do something", model_id="account:codex", mode="ask"
                )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "CONFIG_INVALID")
        self.assertIn("repair", result["answer"].lower())
        self.assertIn("repair_config", result["next_actions"])


# ---------------------------------------------------------------------------
# Bug: Claude streamed a real answer but the card showed the raw init JSON.
# ---------------------------------------------------------------------------
class _Pipe:
    def __init__(self, lines):
        self._lines = list(lines)
        self._i = 0

    def readline(self):
        if self._i < len(self._lines):
            self._i += 1
            return self._lines[self._i - 1]
        return ""  # EOF


class _FakeProc:
    def __init__(self, lines, returncode=0, stderr_lines=None):
        self.stdout = _Pipe(lines)
        self.stderr = _Pipe(stderr_lines or [])
        self.returncode = returncode
        self._alive = True

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return self.returncode


class StreamKeepsTextOnNonZeroExitTests(unittest.TestCase):
    def _runner(self):
        from opaihub.accounts import AccountRunner

        return AccountRunner("claude", "/bin/claude", model="haiku")

    def test_answer_survives_non_zero_exit(self):
        from opaihub import accounts

        lines = [
            '{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello there"}]}}\n',
            '{"type":"result","total_cost_usd":0.01}\n',
        ]
        proc_obj = _FakeProc(lines, returncode=1)
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi")
        self.assertEqual(result["text"], "Hello there")
        self.assertNotIn("error", result)
        self.assertEqual(result["cost"], 0.01)

    def test_no_text_and_non_zero_exit_still_errors(self):
        from opaihub import accounts

        lines = ['{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n']
        proc_obj = _FakeProc(lines, returncode=1)
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi")
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_claude_native_auth_failure_is_never_streamed_as_answer(self):
        from opaihub import accounts

        lines = [
            '{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n',
            '{"type":"result","subtype":"error_during_execution",'
            '"is_error":true,"result":"Failed to authenticate. API Error: 401 '
            'Invalid authentication credentials"}\n',
        ]
        streamed: list[str] = []
        proc_obj = _FakeProc(lines, returncode=1)
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi", on_text=streamed.append)

        self.assertEqual(streamed, [])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        self.assertEqual(
            result["error"]["technicalMessage"],
            "Failed to authenticate. API Error: 401 Invalid authentication credentials",
        )

    def test_codex_turn_failed_is_failure_even_with_zero_exit(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        lines = [
            '{"type":"thread.started","thread_id":"thread_1"}\n',
            '{"type":"turn.failed","error":{"message":'
            '"The model failed before producing a response"}}\n',
        ]
        streamed: list[str] = []
        proc_obj = _FakeProc(lines, returncode=0)
        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.5")
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = runner.stream("hi", on_text=streamed.append)

        self.assertEqual(streamed, [])
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "UNKNOWN")
        self.assertEqual(
            result["error"]["technicalMessage"],
            "The model failed before producing a response",
        )

    def test_codex_failed_turn_never_streams_last_message_file(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        lines = [
            '{"type":"turn.failed","error":{"message":'
            '"401 Invalid authentication credentials"}}\n'
        ]
        proc_obj = _FakeProc(lines, returncode=1)
        streamed: list[str] = []

        def fake_popen(command, *, cwd):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(
                "401 Invalid authentication credentials", encoding="utf-8"
            )
            return proc_obj

        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.5")
        with mock.patch.object(accounts, "_popen", side_effect=fake_popen):
            result = runner.stream("hi", on_text=streamed.append)

        self.assertEqual(streamed, [])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_claude_final_result_does_not_duplicate_streamed_answer(self):
        from opaihub import accounts

        lines = [
            '{"type":"assistant","message":{"content":['
            '{"type":"text","text":"Hello there"}]}}\n',
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"Hello there"}\n',
        ]
        streamed: list[str] = []
        proc_obj = _FakeProc(lines, returncode=0)
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi", on_text=streamed.append)

        self.assertEqual(result["text"], "Hello there")
        self.assertEqual(streamed, ["Hello there"])

    def test_known_stderr_failure_wins_over_partial_text_on_failed_process(self):
        from opaihub import accounts

        lines = [
            '{"type":"assistant","message":{"content":['
            '{"type":"text","text":"Partial answer"}]}}\n',
        ]
        proc_obj = _FakeProc(
            lines,
            returncode=1,
            stderr_lines=["401 Invalid authentication credentials\n"],
        )
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_auth_failure_invalidates_cached_connection_probe(self):
        from opaihub import accounts

        key = ("claude", str(Path.home().expanduser().resolve()))
        accounts._CONNECTION_CACHE[key] = (0.0, {"authStatus": "connected"})
        lines = [
            '{"type":"result","subtype":"error_during_execution",'
            '"is_error":true,"result":"401 Invalid authentication credentials"}\n'
        ]
        proc_obj = _FakeProc(lines, returncode=1)
        try:
            with mock.patch.object(accounts, "_popen", return_value=proc_obj):
                self._runner().stream("hi")
            self.assertNotIn(key, accounts._CONNECTION_CACHE)
        finally:
            accounts._CONNECTION_CACHE.pop(key, None)

    def test_gui_pipeline_has_one_failed_terminal_state_for_native_auth_error(self):
        from opaihub import accounts
        from opaihub.gui_pipeline import handle_gui_message

        lines = [
            '{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n',
            '{"type":"result","subtype":"error_during_execution",'
            '"is_error":true,"result":"Failed to authenticate. API Error: 401 '
            'Invalid authentication credentials"}\n',
        ]
        events: list[dict] = []
        texts: list[str] = []
        proc_obj = _FakeProc(lines, returncode=1)
        runner = self._runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with (
                mock.patch.object(accounts, "_popen", return_value=proc_obj),
                mock.patch.object(runner, "available", return_value=True),
            ):
                result = handle_gui_message(
                    root,
                    "hi",
                    model_id="account:claude:haiku",
                    mode="ask",
                    account_runner=runner,
                    on_event=events.append,
                    on_text=texts.append,
                )

        terminal = [
            event
            for event in events
            if event["type"] in {"provider_auth_failed", "failed", "completed"}
        ]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        self.assertEqual(
            [event["type"] for event in terminal], ["provider_auth_failed"]
        )
        self.assertEqual(texts, [])


class BlockingAccountResultTests(unittest.TestCase):
    def test_claude_json_error_is_not_returned_as_answer(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        completed = mock.Mock(
            returncode=1,
            stdout=(
                '{"type":"result","subtype":"error_during_execution",'
                '"is_error":true,"result":"401 Invalid authentication credentials"}'
            ),
            stderr="",
        )
        runner = AccountRunner("claude", "/bin/claude", model="haiku")
        with mock.patch.object(accounts, "_hidden_run", return_value=completed):
            result = runner.complete("hi")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_codex_failed_process_stderr_is_not_returned_as_answer(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        completed = mock.Mock(
            returncode=1,
            stdout="",
            stderr="OAuth token expired",
        )
        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.5")
        with mock.patch.object(accounts, "_hidden_run", return_value=completed):
            result = runner.complete("hi")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_EXPIRED")

    def test_codex_zero_exit_auth_stderr_is_not_returned_as_answer(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        completed = mock.Mock(
            returncode=0,
            stdout="",
            stderr="401 Invalid authentication credentials",
        )
        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.5")
        with mock.patch.object(accounts, "_hidden_run", return_value=completed):
            result = runner.complete("hi")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_claude_zero_exit_auth_stderr_is_not_returned_as_answer(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        completed = mock.Mock(
            returncode=0,
            stdout="",
            stderr="401 Invalid authentication credentials",
        )
        runner = AccountRunner("claude", "/bin/claude", model="haiku")
        with mock.patch.object(accounts, "_hidden_run", return_value=completed):
            result = runner.complete("hi")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_successful_claude_answer_may_discuss_401_errors(self):
        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        answer = (
            "A 401 Invalid authentication response usually means credentials failed."
        )
        completed = mock.Mock(
            returncode=0,
            stdout=(
                '{"type":"result","subtype":"success","is_error":false,'
                f'"result":{json.dumps(answer)}}}'
            ),
            stderr="",
        )
        runner = AccountRunner("claude", "/bin/claude", model="haiku")
        with mock.patch.object(accounts, "_hidden_run", return_value=completed):
            result = runner.complete("explain HTTP 401")

        self.assertEqual(result["text"], answer)
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
