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

from vestahub import proc


# ---------------------------------------------------------------------------
# Bug (#307): a new-model short id ("sonnet-5") was passed straight to the CLI,
# which rejected it as "model not found". The runner must resolve aliases to the
# provider's canonical, CLI-accepted id.
# ---------------------------------------------------------------------------
class AccountModelResolutionTests(unittest.TestCase):
    def test_alias_resolves_to_the_cli_accepted_canonical_id(self):
        from vestahub.accounts import AccountRunner

        # The stale short id self-heals to the full API id the CLI accepts.
        self.assertEqual(
            AccountRunner("claude", "claude", model="sonnet-5").model,
            "claude-sonnet-5",
        )
        self.assertEqual(
            AccountRunner("claude", "claude", model="fable").model, "claude-fable-5"
        )

    def test_canonical_and_unknown_ids_pass_through(self):
        from vestahub.accounts import AccountRunner

        self.assertEqual(AccountRunner("claude", "claude", model="opus").model, "opus")
        # An id the registry doesn't know is left as-is (the CLI decides).
        self.assertEqual(
            AccountRunner("claude", "claude", model="custom-x").model, "custom-x"
        )
        self.assertEqual(AccountRunner("claude", "claude").model, "")

    def test_the_default_claude_model_is_the_proven_sonnet(self):
        from vesta import model_registry as reg

        # Guards the #307 regression: the default must be a CLI-accepted id, not
        # an unverified new model that breaks the out-of-box experience.
        self.assertEqual(reg.default_model("claude").id, "sonnet")


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
        import vestahub.command_runner as cr

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._assert_passes_no_window(
                cr, lambda: cr.run_policy_command(["git", "status"], root, timeout=5)
            )

    def test_evidence_cache_git_passes_no_window(self):
        import vestahub.evidence_cache as ec

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            if not ec.shutil.which("git"):
                self.skipTest("git not available")
            self._assert_passes_no_window(
                ec, lambda: ec._git_output(root, ["rev-parse", "HEAD"])
            )

    def test_test_select_git_passes_no_window(self):
        import vestahub.test_select as ts

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
        from vestahub.ask import SYSTEM_PROMPT

        low = SYSTEM_PROMPT.lower()
        self.assertIn("greeting", low)
        self.assertIn("relevant", low)
        # It must tell the model NOT to volunteer tests/files for small talk.
        self.assertTrue("test" in low and "not" in low)

    def test_build_prompt_leads_with_user_message_and_optional_context(self):
        from vestahub.ask import _build_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            built = _build_prompt(root, "hello")
        self.assertTrue(built.startswith("User message: hello"))
        low = built.lower()
        self.assertIn("only if it is relevant", low)
        # Context is still available for real coding tasks, just not forced.
        self.assertIn("project context", low)

    def test_capturing_runner_receives_reframed_system_and_prompt(self):
        from vestahub.ask import SYSTEM_PROMPT, run_ask

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

    def test_prose_only_local_runner_rejects_edit_before_cache_or_model_call(self):
        from vestahub.ask import run_ask

        class ProseOnlyRunner:
            name = "ollama"
            model = "qwen-coder"

            def __init__(self):
                self.called = False

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                self.called = True
                return "I changed app.py"

        runner = ProseOnlyRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "vestahub.ask.result_cache.lookup_with_meta"
            ) as cache_lookup:
                result = run_ask(
                    root,
                    "Fix app.py",
                    runner=runner,
                    record=False,
                    allow_edits=True,
                )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["capability"], "edit_files")
        self.assertFalse(runner.called)
        cache_lookup.assert_not_called()


# ---------------------------------------------------------------------------
# Bug: Codex "unknown variant `default`" surfaced as a generic failure.
# ---------------------------------------------------------------------------
class CodexConfigErrorTests(unittest.TestCase):
    def test_unknown_variant_is_config_invalid(self):
        from vesta.provider_contract import classify_error_code

        detail = (
            "Error loading configuration: C:\\Users\\x\\.codex\\config.toml:6:16: "
            "unknown variant `default`, expected `fast` or `flex`"
        )
        self.assertEqual(classify_error_code(detail, returncode=1), "CONFIG_INVALID")

    def test_config_invalid_message_points_to_repair(self):
        from vesta.provider_contract import normalize_provider_error

        detail = (
            "Error loading configuration: unknown variant `default`, expected `fast`"
        )
        err = normalize_provider_error("codex", detail, returncode=1)
        self.assertEqual(err["code"], "CONFIG_INVALID")
        self.assertIn("repair", err["userMessage"].lower())
        self.assertIn("open_settings", err["recoveryActions"])
        self.assertFalse(err["retryable"])

    def test_normal_errors_still_classify_normally(self):
        from vesta.provider_contract import classify_error_code

        self.assertEqual(
            classify_error_code("401 Invalid authentication credentials"),
            "AUTH_INVALID",
        )

    def test_config_invalid_offers_repair_action(self):
        from vesta.provider_contract import normalize_provider_error

        err = normalize_provider_error(
            "codex", "unknown variant `default`, expected `fast`", returncode=1
        )
        self.assertIn("repair_config", err["recoveryActions"])

    def _codex_config_error(self):
        from vesta.provider_contract import normalize_provider_error

        return normalize_provider_error(
            "codex",
            "Error loading configuration: unknown variant `default`, expected `fast`",
            returncode=1,
        )

    def test_connection_surfaces_config_error_not_cli_unavailable(self):
        # The misconfigured diagnostic must reflect the real config problem and
        # carry the structured error, not the misleading "CLI unavailable".
        from vestahub.accounts import connection_for_account

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
        from vestahub.gui_pipeline import handle_gui_message
        from vestahub.accounts import connection_for_account

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
                "vestahub.accounts.test_account_connection", return_value=conn
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
        from vestahub.accounts import AccountRunner

        return AccountRunner("claude", "/bin/claude", model="haiku")

    def test_answer_survives_non_zero_exit(self):
        from vestahub import accounts

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
        from vestahub import accounts

        lines = ['{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n']
        proc_obj = _FakeProc(lines, returncode=1)
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = self._runner().stream("hi")
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)

    def test_claude_native_auth_failure_is_never_streamed_as_answer(self):
        from vestahub import accounts

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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

    def test_codex_completed_messages_preserve_stream_block_boundaries(self):
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

        lines = [
            '{"type":"thread.started","thread_id":"thread_1"}\n',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"First update."}}\n',
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"Final update."}}\n',
            '{"type":"turn.completed"}\n',
        ]
        received: list[tuple[str, bool]] = []

        def on_text(text: str, start_block: bool = False) -> None:
            received.append((text, start_block))

        setattr(on_text, "accepts_block_start", True)
        proc_obj = _FakeProc(lines, returncode=0)
        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.5")
        with mock.patch.object(accounts, "_popen", return_value=proc_obj):
            result = runner.stream("hi", on_text=on_text)

        self.assertEqual(
            received,
            [("First update.", False), ("Final update.", True)],
        )
        self.assertEqual(result["text"], "First update.\n\nFinal update.")

    def test_codex_failed_turn_never_streams_last_message_file(self):
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

        lines = [
            '{"type":"turn.failed","error":{"message":'
            '"401 Invalid authentication credentials"}}\n'
        ]
        proc_obj = _FakeProc(lines, returncode=1)
        streamed: list[str] = []

        def fake_popen(command, *, cwd, env=None):
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
        from vestahub import accounts

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
        from vestahub import accounts

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

        # A typed failure remains authoritative, but already-observed streamed
        # content must remain available for the canonical partial-stream state.
        self.assertEqual(result["text"], "Partial answer")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")

    def test_auth_failure_invalidates_cached_connection_probe(self):
        from vestahub import accounts

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
        from vestahub import accounts
        from vestahub.gui_pipeline import handle_gui_message

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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
        from vestahub import accounts
        from vestahub.accounts import AccountRunner

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


# ---------------------------------------------------------------------------
# Bug: Vesta says "connected" (Claude auth status / cache) but a real send 401s,
# and the cached "connected" verdict then keeps lying for up to 5 minutes.
# ---------------------------------------------------------------------------
class ConnectionCacheInvalidationTests(unittest.TestCase):
    def _prime_cache(self, account_id: str, *, home) -> None:
        """Populate the connection cache the same way a real check would."""
        from vestahub import accounts

        with (
            mock.patch.object(
                accounts,
                "list_connected_accounts",
                return_value=[
                    {
                        "id": account_id,
                        "label": account_id.capitalize(),
                        "cli_present": True,
                        "authenticated": True,
                        "connected": True,
                        "login_hint": "hint",
                    }
                ],
            ),
            mock.patch.object(
                accounts,
                "_hidden_run",
                return_value=mock.Mock(returncode=0, stdout="ok", stderr=""),
            ),
        ):
            result = accounts.test_account_connection(account_id, home=home)
        self.assertEqual(result["authStatus"], "connected")

    def _cache_has(self, account_id: str) -> bool:
        from vestahub.accounts import _CONNECTION_CACHE

        return any(key[0] == account_id for key in _CONNECTION_CACHE)

    def test_invalidate_clears_only_the_matching_provider(self):
        from vestahub.accounts import invalidate_connection_cache

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._prime_cache("claude", home=home)
            self._prime_cache("codex", home=home)
            self.assertTrue(self._cache_has("claude"))
            self.assertTrue(self._cache_has("codex"))
            invalidate_connection_cache("claude")
            self.assertFalse(self._cache_has("claude"))
            self.assertTrue(self._cache_has("codex"), "unrelated provider must survive")

    def test_ask_account_auth_failure_busts_the_stale_connected_cache(self):
        # _ask_account never calls test_account_connection itself (that
        # pre-flight lives in gui_pipeline); invalidate_connection_cache
        # matches purely on account_id, so priming under any home still
        # proves a genuine 401 clears the entry regardless of which check
        # populated it.
        from vesta.app_state import ask

        with (
            tempfile.TemporaryDirectory() as home_tmp,
            tempfile.TemporaryDirectory() as repo_tmp,
        ):
            self._prime_cache("claude", home=Path(home_tmp))
            self.assertTrue(self._cache_has("claude"))

            root = make_repo(Path(repo_tmp))
            result = ask(
                root,
                "task",
                model_choice="account:claude:sonnet",
                allow_edits=False,
                account_runner=FakeAccountRunnerAuthFail(),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "AUTH_INVALID")
            self.assertFalse(
                self._cache_has("claude"),
                "a genuine 401 must invalidate the stale 'connected' cache entry",
            )

    def test_non_auth_failure_does_not_touch_the_cache(self):
        from vesta.app_state import ask

        class _TimeoutRunner:
            paid = True

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                return {"text": "", "cost": None, "timed_out": True}

        with (
            tempfile.TemporaryDirectory() as home_tmp,
            tempfile.TemporaryDirectory() as repo_tmp,
        ):
            self._prime_cache("claude", home=Path(home_tmp))

            root = make_repo(Path(repo_tmp))
            result = ask(
                root,
                "task",
                model_choice="account:claude:sonnet",
                account_runner=_TimeoutRunner(),
            )
            self.assertEqual(result["error"]["code"], "PROVIDER_TIMEOUT")
            self.assertTrue(
                self._cache_has("claude"),
                "a non-auth failure (timeout) must not bust an unrelated cache entry",
            )


class FakeAccountRunnerAuthFail:
    """A connected-looking runner whose real completion 401s (the reported bug)."""

    paid = True

    def available(self):
        return True

    def complete(self, prompt, **kwargs):
        raise RuntimeError("API Error: 401 Invalid authentication credentials")


# ---------------------------------------------------------------------------
# Feature: a real Disconnect action, because a session that passes every local
# check but keeps 401ing has no other way to force a clean re-login.
# ---------------------------------------------------------------------------
class DisconnectAccountTests(unittest.TestCase):
    def test_unknown_provider_reports_cleanly(self):
        from vestahub.accounts import disconnect_account

        result = disconnect_account("not-a-real-provider")
        self.assertFalse(result["disconnected"])
        self.assertIn("Unknown provider", result["message"])

    def test_copilot_has_no_cli_logout_and_says_so_honestly(self):
        # Verified against the real copilot --help: only `login` is listed,
        # no `logout` subcommand — Vesta must not fabricate one.
        from vestahub.accounts import disconnect_account

        result = disconnect_account("copilot")
        self.assertFalse(result["disconnected"])
        self.assertTrue(result.get("unsupported"))
        self.assertIn("no command-line sign-out", result["message"])
        self.assertIn("copilot", result["message"])

    def test_cli_missing_from_path_reports_cleanly(self):
        from vestahub import accounts

        with mock.patch.object(accounts, "_which", return_value=None):
            result = accounts.disconnect_account("claude")
        self.assertFalse(result["disconnected"])
        self.assertIn("not found on PATH", result["message"])

    def test_claude_logout_success_invalidates_cache_and_reports_signed_out(self):
        from vestahub import accounts

        with (
            mock.patch.object(accounts, "_which", return_value="/bin/claude"),
            mock.patch.object(
                accounts,
                "_hidden_run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ) as hidden_run,
        ):
            # Prime the cache the same way a real check would, then prove
            # disconnect clears it.
            with mock.patch.object(
                accounts,
                "list_connected_accounts",
                return_value=[
                    {
                        "id": "claude",
                        "label": "Claude",
                        "cli_present": True,
                        "authenticated": True,
                        "connected": True,
                        "login_hint": "hint",
                    }
                ],
            ):
                accounts.test_account_connection(
                    "claude", home=Path(tempfile.mkdtemp())
                )
            hidden_run.reset_mock()  # priming also calls _hidden_run once; isolate disconnect's call
            result = accounts.disconnect_account("claude")

        self.assertTrue(result["disconnected"])
        self.assertIn("Signed out", result["message"])
        self.assertIn("claude", result["message"])
        # The exact CLI-documented subcommand, not a guess.
        hidden_run.assert_called_once()
        argv = hidden_run.call_args.args[0]
        self.assertEqual(argv[-2:], ["auth", "logout"])
        self.assertFalse(
            any(key[0] == "claude" for key in accounts._CONNECTION_CACHE),
            "disconnect must clear the cached 'connected' verdict",
        )

    def test_codex_logout_uses_documented_subcommand(self):
        from vestahub import accounts

        with (
            mock.patch.object(accounts, "_which", return_value="/bin/codex"),
            mock.patch.object(
                accounts,
                "_hidden_run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ) as hidden_run,
        ):
            result = accounts.disconnect_account("codex")
        self.assertTrue(result["disconnected"])
        self.assertEqual(hidden_run.call_args.args[0][-1], "logout")

    def test_logout_failure_surfaces_cli_output_not_a_false_success(self):
        from vestahub import accounts

        with (
            mock.patch.object(accounts, "_which", return_value="/bin/claude"),
            mock.patch.object(
                accounts,
                "_hidden_run",
                return_value=mock.Mock(
                    returncode=1, stdout="", stderr="network unreachable"
                ),
            ),
        ):
            result = accounts.disconnect_account("claude")
        self.assertFalse(result["disconnected"])
        self.assertIn("network unreachable", result["message"])


if __name__ == "__main__":
    unittest.main()
