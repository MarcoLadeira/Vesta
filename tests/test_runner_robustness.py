"""AccountRunner build_command, complete(), hidden-run flags, and detection.

No real CLI is ever invoked - _hidden_run is always patched. Detection tests
use isolated_home() so the developer's real auth files cannot leak in.
Covers: argv correctness, JSON/non-JSON/empty/timeout/stderr output, hidden
flags (CREATE_NO_WINDOW, DEVNULL, utf-8/replace), and account discovery.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from _helpers import isolated_home
from opaihub.accounts import (
    CLAUDE_MODELS,
    CODEX_MODELS,
    AccountRunner,
    account_models,
    list_connected_accounts,
    runner_for_account,
)
from opaihub.local_models import classify_endpoint

_CLAUDE_CLI = "/fake/claude"
_CODEX_CLI = "/fake/codex"


# ---------------------------------------------------------------------------
# 1. build_command - pure, no subprocess
# ---------------------------------------------------------------------------
class ClaudeBuildCommandTests(unittest.TestCase):
    def _runner(self, model: str = "sonnet") -> AccountRunner:
        return AccountRunner("claude", _CLAUDE_CLI, model=model)

    def test_base_includes_json_output_flag(self):
        cmd = self._runner().build_command("hello")
        self.assertIn("--output-format", cmd)
        idx = cmd.index("--output-format")
        self.assertEqual(cmd[idx + 1], "json")

    def test_negative_p_flag_present(self):
        cmd = self._runner().build_command("hello")
        self.assertIn("-p", cmd)

    def test_model_alias_is_passed_when_set(self):
        cmd = self._runner("opus").build_command("hi")
        self.assertIn("--model", cmd)
        idx = cmd.index("--model")
        self.assertEqual(cmd[idx + 1], "opus")

    def test_no_model_omits_model_flag(self):
        runner = AccountRunner("claude", _CLAUDE_CLI, model="")
        cmd = runner.build_command("hi")
        self.assertNotIn("--model", cmd)

    def test_full_auto_adds_dangerously_skip(self):
        cmd = self._runner().build_command("hi", allow_edits=True, mode="full-auto")
        self.assertIn("--dangerously-skip-permissions", cmd)

    def test_safe_auto_does_not_add_dangerously_skip(self):
        cmd = self._runner().build_command("hi", allow_edits=True, mode="safe-auto")
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_ask_does_not_add_dangerously_skip(self):
        cmd = self._runner().build_command("hi", allow_edits=False, mode="ask")
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_plan_does_not_add_dangerously_skip(self):
        cmd = self._runner().build_command("hi", allow_edits=False, mode="plan")
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_approve_edits_does_not_add_dangerously_skip(self):
        cmd = self._runner().build_command(
            "hi", allow_edits=False, mode="approve-edits"
        )
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_read_only_modes_prepend_no_modify_instruction(self):
        for mode in ("ask", "plan", "approve-edits"):
            with self.subTest(mode=mode):
                cmd = self._runner().build_command("my task", mode=mode)
                prompt_arg = cmd[-1]
                self.assertIn("Do not modify files", prompt_arg)
                self.assertIn("my task", prompt_arg)

    def test_edit_modes_do_not_prepend_read_only_instruction(self):
        for mode in ("safe-auto", "full-auto"):
            with self.subTest(mode=mode):
                cmd = self._runner().build_command(
                    "my task", allow_edits=True, mode=mode
                )
                prompt_arg = cmd[-1]
                self.assertNotIn("Do not modify files", prompt_arg)

    def test_prompt_is_last_arg(self):
        cmd = self._runner().build_command("do the thing")
        self.assertIn("do the thing", cmd[-1])

    def test_haiku_model_flag(self):
        cmd = self._runner("haiku").build_command("hi")
        idx = cmd.index("--model")
        self.assertEqual(cmd[idx + 1], "haiku")


class CodexBuildCommandTests(unittest.TestCase):
    def _runner(self, model: str = "gpt-5.5") -> AccountRunner:
        return AccountRunner("codex", _CODEX_CLI, model=model)

    def test_exec_subcommand_present(self):
        cmd = self._runner().build_command("hi")
        self.assertIn("exec", cmd)

    def test_read_only_sandbox_for_ask(self):
        cmd = self._runner().build_command("hi", mode="ask")
        idx = cmd.index("--sandbox")
        self.assertEqual(cmd[idx + 1], "read-only")

    def test_read_only_sandbox_for_plan(self):
        cmd = self._runner().build_command("hi", mode="plan")
        idx = cmd.index("--sandbox")
        self.assertEqual(cmd[idx + 1], "read-only")

    def test_workspace_write_sandbox_for_safe_auto(self):
        cmd = self._runner().build_command("hi", allow_edits=True, mode="safe-auto")
        idx = cmd.index("--sandbox")
        self.assertEqual(cmd[idx + 1], "workspace-write")

    def test_workspace_write_sandbox_for_full_auto(self):
        cmd = self._runner().build_command("hi", allow_edits=True, mode="full-auto")
        idx = cmd.index("--sandbox")
        self.assertEqual(cmd[idx + 1], "workspace-write")

    def test_full_auto_uses_never_approval(self):
        cmd = self._runner().build_command("hi", mode="full-auto")
        idx = cmd.index("--ask-for-approval")
        self.assertEqual(cmd[idx + 1], "never")

    def test_non_full_auto_uses_on_request_approval(self):
        for mode in ("ask", "plan", "safe-auto", "approve-edits"):
            with self.subTest(mode=mode):
                cmd = self._runner().build_command("hi", mode=mode)
                idx = cmd.index("--ask-for-approval")
                self.assertEqual(cmd[idx + 1], "on-request")

    def test_out_file_arg_is_included(self):
        cmd = self._runner().build_command("hi", out_file="/tmp/out.txt")
        self.assertIn("--output-last-message", cmd)
        self.assertIn("/tmp/out.txt", cmd)

    def test_out_file_omitted_when_none(self):
        cmd = self._runner().build_command("hi")
        self.assertNotIn("--output-last-message", cmd)

    def test_model_flag_included(self):
        cmd = self._runner("gpt-5.4-mini").build_command("hi")
        self.assertIn("--model", cmd)
        idx = cmd.index("--model")
        self.assertEqual(cmd[idx + 1], "gpt-5.4-mini")

    def test_color_never_always_present(self):
        cmd = self._runner().build_command("hi")
        idx = cmd.index("--color")
        self.assertEqual(cmd[idx + 1], "never")

    def test_skip_git_repo_check_always_present(self):
        cmd = self._runner().build_command("hi")
        self.assertIn("--skip-git-repo-check", cmd)

    def test_prompt_is_last_arg(self):
        cmd = self._runner().build_command("do the thing")
        self.assertIn("do the thing", cmd[-1])


class UnknownAccountBuildCommandTests(unittest.TestCase):
    def test_unknown_account_raises_value_error(self):
        runner = AccountRunner("unknown_ai", "/fake/cli")
        with self.assertRaises(ValueError):
            runner.build_command("hi")


# ---------------------------------------------------------------------------
# 2. complete() for claude - _hidden_run is always patched
# ---------------------------------------------------------------------------
class ClaudeCompleteTests(unittest.TestCase):
    def _run(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: Exception | None = None,
    ) -> dict:
        runner = AccountRunner("claude", _CLAUDE_CLI, model="sonnet")
        ret = mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)
        if raises is not None:
            with mock.patch("opaihub.accounts._hidden_run", side_effect=raises):
                return runner.complete("task")
        with mock.patch("opaihub.accounts._hidden_run", return_value=ret):
            return runner.complete("task")

    def test_clean_json_returns_text_and_cost(self):
        payload = json.dumps({"result": "great answer", "total_cost_usd": 0.042})
        res = self._run(payload)
        self.assertEqual(res["text"], "great answer")
        self.assertAlmostEqual(res["cost"], 0.042)

    def test_total_cost_usd_preserved_as_cost(self):
        payload = json.dumps({"result": "ok", "total_cost_usd": 0.123})
        res = self._run(payload)
        self.assertAlmostEqual(res["cost"], 0.123)

    def test_non_json_returns_raw_text(self):
        res = self._run("some plain text output")
        self.assertEqual(res["text"], "some plain text output")
        self.assertIsNone(res["cost"])

    def test_empty_stdout_falls_back_to_stderr(self):
        res = self._run(stdout="", stderr="error hint", returncode=1)
        self.assertEqual(res["text"], "error hint")

    def test_timeout_returns_timed_out_flag_not_exception(self):
        res = self._run(
            raises=subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=1200)
        )
        self.assertTrue(res.get("timed_out"))
        self.assertNotIn("TimeoutExpired", str(res))

    def test_no_raw_command_leaked_on_timeout(self):
        res = self._run(
            raises=subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=1200)
        )
        text = str(res)
        self.assertNotIn("'-p'", text)
        self.assertNotIn("TimeoutExpired", text)

    def test_json_missing_result_falls_back_to_raw_json(self):
        payload = json.dumps({"total_cost_usd": 0.01})
        res = self._run(payload)
        self.assertTrue(res["text"], "should not be empty")

    def test_non_zero_exit_still_returns_text(self):
        res = self._run(stdout="partial output", returncode=1)
        self.assertEqual(res["text"], "partial output")


# ---------------------------------------------------------------------------
# 3. complete() for codex - _hidden_run is always patched
# ---------------------------------------------------------------------------
class CodexCompleteTests(unittest.TestCase):
    def _run(
        self,
        out_content: str | None = "codex answer",
        stdout: str = "",
        raises: Exception | None = None,
    ) -> dict:
        runner = AccountRunner("codex", _CODEX_CLI, model="gpt-5.5")

        def fake_hidden_run(cmd, *, cwd, timeout):
            out_file = None
            for i, arg in enumerate(cmd):
                if arg == "--output-last-message" and i + 1 < len(cmd):
                    out_file = cmd[i + 1]
            if out_file is not None and out_content is not None:
                Path(out_file).write_text(out_content, encoding="utf-8")
            if raises:
                raise raises
            return mock.Mock(stdout=stdout, stderr="", returncode=0)

        with mock.patch("opaihub.accounts._hidden_run", side_effect=fake_hidden_run):
            return runner.complete("task")

    def test_reads_answer_from_out_file(self):
        res = self._run(out_content="codex result")
        self.assertEqual(res["text"], "codex result")

    def test_empty_out_file_falls_back_to_stdout(self):
        res = self._run(out_content=None, stdout="fallback text")
        self.assertIn("fallback text", res["text"])

    def test_timeout_returns_timed_out_flag(self):
        res = self._run(raises=subprocess.TimeoutExpired(cmd=[], timeout=1200))
        self.assertTrue(res.get("timed_out"))

    def test_no_raw_command_on_timeout(self):
        res = self._run(raises=subprocess.TimeoutExpired(cmd=["codex"], timeout=1200))
        text = str(res)
        self.assertNotIn("TimeoutExpired", text)

    def test_cost_is_none_for_codex(self):
        res = self._run(out_content="answer")
        self.assertIsNone(res["cost"])


# ---------------------------------------------------------------------------
# 4. _hidden_run flags: DEVNULL, utf-8/replace, CREATE_NO_WINDOW
# ---------------------------------------------------------------------------
class HiddenRunFlagTests(unittest.TestCase):
    def _call_kwargs(self) -> dict:
        from opaihub.accounts import _hidden_run

        with mock.patch("subprocess.run") as mocked:
            mocked.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            _hidden_run(["echo", "hi"], cwd=None, timeout=5)
            return mocked.call_args[1]

    def test_stdin_is_devnull(self):
        kwargs = self._call_kwargs()
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_utf8_encoding(self):
        kwargs = self._call_kwargs()
        self.assertEqual(kwargs["encoding"], "utf-8")

    def test_replace_error_mode(self):
        kwargs = self._call_kwargs()
        self.assertEqual(kwargs["errors"], "replace")

    def test_capture_output_true(self):
        kwargs = self._call_kwargs()
        self.assertTrue(kwargs["capture_output"])

    @unittest.skipUnless(sys.platform == "win32", "windows-only flag")
    def test_create_no_window_on_windows(self):
        kwargs = self._call_kwargs()
        self.assertIn("creationflags", kwargs)
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    @unittest.skipIf(sys.platform == "win32", "posix-only: no creationflags expected")
    def test_no_creation_flags_on_posix(self):
        kwargs = self._call_kwargs()
        self.assertNotIn("creationflags", kwargs)


# ---------------------------------------------------------------------------
# 5. Account detection: list_connected_accounts, account_models, runner_for_account
# ---------------------------------------------------------------------------
class DetectionTests(unittest.TestCase):
    def test_empty_home_nothing_connected(self):
        with isolated_home() as home:
            accounts = list_connected_accounts(home)
            for account in accounts:
                self.assertFalse(
                    account["connected"],
                    f"{account['id']} should not be connected with empty home",
                )

    def test_claude_credentials_json_means_connected(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                accounts = list_connected_accounts(home)
            claude = next(a for a in accounts if a["id"] == "claude")
            self.assertTrue(claude["connected"])
            self.assertTrue(claude["authenticated"])
            self.assertTrue(claude["cli_present"])

    def test_claude_json_in_home_means_connected(self):
        with isolated_home() as home:
            (home / ".claude.json").write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                accounts = list_connected_accounts(home)
            claude = next(a for a in accounts if a["id"] == "claude")
            self.assertTrue(claude["connected"])

    def test_auth_file_present_cli_missing_not_connected(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value=None):
                accounts = list_connected_accounts(home)
            claude = next(a for a in accounts if a["id"] == "claude")
            self.assertFalse(claude["connected"])
            self.assertFalse(claude["cli_present"])

    def test_codex_auth_json_means_connected(self):
        with isolated_home() as home:
            auth = home / ".codex" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/codex"):
                accounts = list_connected_accounts(home)
            codex = next(a for a in accounts if a["id"] == "codex")
            self.assertTrue(codex["connected"])

    def test_account_models_empty_when_nothing_connected(self):
        with isolated_home() as home:
            options = account_models(home)
            self.assertEqual(options, [])

    def test_include_unavailable_returns_all_entries(self):
        with isolated_home() as home:
            options = account_models(home, include_unavailable=True)
            ids = [o["id"] for o in options]
            self.assertTrue(any("claude" in i for i in ids))
            self.assertTrue(any("codex" in i for i in ids))
            for opt in options:
                self.assertFalse(opt["available"])

    def test_claude_expands_to_three_models(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                options = account_models(home)
            claude_opts = [o for o in options if o["provider"] == "claude"]
            self.assertEqual(len(claude_opts), len(CLAUDE_MODELS))

    def test_claude_models_include_sonnet_opus_haiku(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                options = account_models(home)
            models = {o["model"] for o in options if o["provider"] == "claude"}
            self.assertIn("sonnet", models)
            self.assertIn("opus", models)
            self.assertIn("haiku", models)

    def test_codex_expands_to_multiple_models(self):
        with isolated_home() as home:
            auth = home / ".codex" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/codex"):
                options = account_models(home)
            codex_opts = [o for o in options if o["provider"] == "codex"]
            self.assertGreater(len(codex_opts), 1)
            self.assertEqual(len(codex_opts), len(CODEX_MODELS))

    def test_runner_for_account_none_when_not_connected(self):
        with isolated_home() as home:
            runner = runner_for_account("claude", home=home)
            self.assertIsNone(runner)

    def test_runner_for_account_returns_runner_when_connected(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                runner = runner_for_account("claude", home=home)
            self.assertIsNotNone(runner)
            self.assertEqual(runner.account_id, "claude")  # type: ignore[union-attr]

    def test_runner_model_is_passed_through(self):
        with isolated_home() as home:
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir(parents=True)
            auth.write_text("{}", encoding="utf-8")
            with mock.patch("opaihub.accounts._which", return_value="/usr/bin/claude"):
                runner = runner_for_account("claude", model="haiku", home=home)
            self.assertIsNotNone(runner)
            self.assertEqual(runner.model, "haiku")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6. classify_endpoint - no network calls
# ---------------------------------------------------------------------------
class ClassifyEndpointTests(unittest.TestCase):
    def test_localhost_is_local(self):
        self.assertTrue(classify_endpoint("http://localhost:11434")["is_local"])

    def test_127_0_0_1_is_local(self):
        self.assertTrue(classify_endpoint("http://127.0.0.1:11434")["is_local"])

    def test_public_https_is_not_local(self):
        result = classify_endpoint("https://api.openai.com/v1")
        self.assertFalse(result["is_local"])
        self.assertEqual(result["classification"], "public")

    def test_private_rfc1918_is_local(self):
        self.assertTrue(classify_endpoint("http://192.168.1.10:11434")["is_local"])

    def test_dot_local_is_local(self):
        self.assertTrue(classify_endpoint("http://mybox.local:11434")["is_local"])

    def test_empty_url_is_not_local(self):
        result = classify_endpoint("")
        self.assertFalse(result["is_local"])
        self.assertEqual(result["classification"], "empty")

    def test_public_endpoint_requires_cloud_confirmation(self):
        result = classify_endpoint("https://example.com/api")
        self.assertTrue(result.get("requires_cloud_confirmation"))


if __name__ == "__main__":
    unittest.main()
