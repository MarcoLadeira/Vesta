"""Provider CLI spawns get a sanitized environment (the auth-truth fix).

The reported failure class: OPai launched from a terminal where another AI
session runs (Claude Code, Codex, an agent harness). The child provider CLI
inherits that parent's session variables — CLAUDECODE, CLAUDE_CODE_SDK_HAS_
OAUTH_REFRESH, stale ANTHROPIC_API_KEY/OPENAI_API_KEY overrides — so
`claude auth status` reports the PARENT's session ("logged in · pro") while
the real completion call fails (401 / "Not logged in · Please run /login").

These tests lock in: sanitization strips exactly the hijacking variables and
nothing else; the sanitized env reaches every CLI spawn (status probe, run,
stream, logout); removed names are reported for diagnostics but VALUES are
never leaked; and the CLI's "Not logged in" sentinel is surfaced as an auth
error, never rendered as a normal answer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import accounts
from opaihub.proc import AGENT_SESSION_ENV, provider_child_env

SECRET = "sk-super-secret-value-123456789"  # pragma: allowlist secret


class ProviderChildEnvTests(unittest.TestCase):
    def _base(self) -> dict[str, str]:
        return {
            "PATH": r"C:\Windows",
            "HOME": r"C:\Users\t",
            "GOOGLE_API_KEY": "gemini-key",
            "GH_TOKEN": "gh-token",
            "ANTHROPIC_API_KEY": SECRET,
            "ANTHROPIC_BASE_URL": "https://proxy.example",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "abc",
            "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH": "1",
            "OPENAI_API_KEY": "openai-key",
        }

    def test_claude_strips_hijacking_vars_and_keeps_the_rest(self):
        env, removed = provider_child_env("claude", self._base())
        for gone in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
        ):
            self.assertNotIn(gone, env)
            self.assertIn(gone, removed)
        # Everything unrelated survives — including other providers' keys.
        for kept in ("PATH", "HOME", "GOOGLE_API_KEY", "GH_TOKEN", "OPENAI_API_KEY"):
            self.assertIn(kept, env)

    def test_removed_is_sorted_names_only_never_values(self):
        _env, removed = provider_child_env("claude", self._base())
        self.assertEqual(removed, sorted(removed))
        self.assertNotIn(SECRET, json.dumps(removed))

    def test_codex_strips_openai_overrides_keeps_anthropic_and_gh(self):
        env, removed = provider_child_env("codex", self._base())
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertIn("OPENAI_API_KEY", removed)
        self.assertIn("ANTHROPIC_API_KEY", env)  # claude-only concern
        self.assertIn("GH_TOKEN", env)

    def test_copilot_and_unknown_providers_strip_nothing(self):
        from opaihub.command_consent import consent_dir
        from opaihub.proc import COMMAND_CONSENT_DIR_ENV

        for provider in ("copilot", "mystery", ""):
            env, removed = provider_child_env(provider, self._base())
            self.assertEqual(removed, [])
            # Nothing is stripped; the additions are the recursion-guard session
            # marker (F12) and the pinned approval-handshake directory the child's
            # PreToolUse hook reads the one-shot push grant from (Round 5).
            self.assertEqual(
                env,
                {
                    **self._base(),
                    AGENT_SESSION_ENV: "1",
                    COMMAND_CONSENT_DIR_ENV: str(consent_dir()),
                },
            )

    def test_default_base_reads_current_environment(self):
        with mock.patch.dict(
            "os.environ", {"CLAUDE_CODE_TEST_MARKER": "x"}, clear=False
        ):
            _env, removed = provider_child_env("claude")
        self.assertIn("CLAUDE_CODE_TEST_MARKER", removed)


class _Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class SpawnsUseSanitizedEnvTests(unittest.TestCase):
    """The sanitized env must reach every real CLI spawn path."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": SECRET, "CLAUDECODE": "1"},
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _runner(self) -> accounts.AccountRunner:
        return accounts.AccountRunner("claude", "/bin/claude", model="haiku")

    def test_complete_spawns_with_sanitized_env(self):
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _Done(stdout='{"result":"hi","total_cost_usd":0.0}')

        with mock.patch.object(accounts.subprocess, "run", side_effect=fake_run):
            result = self._runner().complete("hello")
        self.assertEqual(result["text"], "hi")
        env = captured.get("env")
        self.assertIsNotNone(env, "complete() must pass an explicit child env")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertIn("PATH", env)

    def test_stream_spawns_with_sanitized_env(self):
        captured: dict = {}

        class _Pipe:
            def __init__(self, lines):
                self._lines = list(lines)

            def readline(self):
                return self._lines.pop(0) if self._lines else ""

        class _Proc:
            stdout = _Pipe(['{"type":"result","result":"hi","total_cost_usd":0.01}\n'])
            stderr = _Pipe([])
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def fake_popen(cmd, **kwargs):
            captured.update(kwargs)
            return _Proc()

        with mock.patch.object(accounts.subprocess, "Popen", side_effect=fake_popen):
            result = self._runner().stream("hello")
        self.assertEqual(result["text"], "hi")
        env = captured.get("env")
        self.assertIsNotNone(env, "stream() must pass an explicit child env")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDECODE", env)

    def test_status_probe_spawns_with_sanitized_env_and_reports_names(self):
        captured: dict = {}

        def fake_hidden_run(cmd, *, cwd, timeout, env=None):
            captured["env"] = env
            return _Done(stdout='{"loggedIn": true}', returncode=0)

        fake_account = {
            "id": "claude",
            "label": "Claude",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "cli_path": "/bin/claude",
            "login_hint": "hint",
        }
        with (
            mock.patch.object(
                accounts, "list_connected_accounts", return_value=[fake_account]
            ),
            mock.patch.object(accounts, "_hidden_run", side_effect=fake_hidden_run),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                payload = accounts.test_account_connection(
                    "claude", home=Path(tmp), force=True
                )
        env = captured.get("env")
        self.assertIsNotNone(env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(payload["authStatus"], "connected")
        self.assertIn("ANTHROPIC_API_KEY", payload["envOverridesRemoved"])
        self.assertIn("CLAUDECODE", payload["envOverridesRemoved"])
        # Names only — the secret value must never appear anywhere.
        self.assertNotIn(SECRET, json.dumps(payload))

    def test_disconnect_spawns_with_sanitized_env(self):
        captured: dict = {}

        def fake_hidden_run(cmd, *, cwd, timeout, env=None):
            captured["env"] = env
            return _Done(returncode=0)

        with (
            mock.patch.object(accounts, "_which", return_value="/bin/claude"),
            mock.patch.object(accounts, "_hidden_run", side_effect=fake_hidden_run),
        ):
            result = accounts.disconnect_account("claude")
        self.assertTrue(result["disconnected"])
        self.assertIsNotNone(captured.get("env"))
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])


class LoginSentinelTests(unittest.TestCase):
    """'Not logged in · Please run /login' is an auth error, never an answer."""

    SENTINEL = "Not logged in · Please run /login"

    def test_is_login_sentinel_matches_the_cli_message(self):
        self.assertTrue(accounts._is_login_sentinel(self.SENTINEL))
        self.assertTrue(accounts._is_login_sentinel("not logged in"))

    def test_is_login_sentinel_never_swallows_real_answers(self):
        long_answer = "Not logged in is what the server replies when " + "x" * 120
        self.assertFalse(accounts._is_login_sentinel(long_answer))
        self.assertFalse(accounts._is_login_sentinel("The user is not logged in."))
        self.assertFalse(accounts._is_login_sentinel(""))

    def test_classifier_maps_sentinel_to_auth_missing(self):
        from opai.provider_contract import classify_error_code

        self.assertEqual(classify_error_code(self.SENTINEL), "AUTH_MISSING")
        # Plain "not logged in" (the status-probe detail) keeps its mapping.
        self.assertEqual(classify_error_code("Not logged in"), "AUTH_INVALID")

    def _runner(self) -> accounts.AccountRunner:
        return accounts.AccountRunner("claude", "/bin/claude", model="haiku")

    def test_complete_surfaces_sentinel_as_auth_error(self):
        payload = json.dumps({"result": self.SENTINEL})

        with mock.patch.object(
            accounts.subprocess,
            "run",
            return_value=_Done(stdout=payload, returncode=1),
        ):
            result = self._runner().complete("hello")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_MISSING")

    def test_complete_surfaces_sentinel_even_on_exit_zero(self):
        payload = json.dumps({"result": self.SENTINEL})

        with mock.patch.object(
            accounts.subprocess,
            "run",
            return_value=_Done(stdout=payload, returncode=0),
        ):
            result = self._runner().complete("hello")
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_stream_surfaces_sentinel_as_auth_error_not_answer(self):
        class _Pipe:
            def __init__(self, lines):
                self._lines = list(lines)

            def readline(self):
                return self._lines.pop(0) if self._lines else ""

        class _Proc:
            stdout = _Pipe(
                [
                    '{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n',
                    json.dumps({"type": "result", "result": self.SENTINEL}) + "\n",
                ]
            )
            stderr = _Pipe([])
            returncode = 1

            def poll(self):
                return 1

            def wait(self, timeout=None):
                return 1

        with mock.patch.object(accounts, "_popen", return_value=_Proc()):
            result = self._runner().stream("hello")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_MISSING")


if __name__ == "__main__":
    unittest.main()
