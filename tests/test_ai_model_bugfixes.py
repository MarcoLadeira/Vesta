"""Regressions for the "talking to AI models is broken" bug batch.

Covers: no flashing console windows on Windows (subprocess CREATE_NO_WINDOW),
free-tier/local chat not being polluted with test-run instructions, the Codex
invalid-config error surfacing a repair path, and a streamed answer surviving a
non-zero CLI exit. No real CLI, model, or network is used.
"""

from __future__ import annotations

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
    def __init__(self, lines, returncode=0):
        self.stdout = _Pipe(lines)
        self.stderr = _Pipe([])
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


if __name__ == "__main__":
    unittest.main()
