"""Provider-aware shell-wrapper launch contract for issue #114."""

from __future__ import annotations

import tempfile
import unittest
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo
from opaihub.agent_launch import (
    PASSTHROUGH_EXIT,
    classify_invocation,
    launch_agent,
)
from opaihub.ledger import EVENT_CAPTURE_SESSION, read_events
from opai.integrations import install_global_integrations
from opai.gui_view_model import _client_card


def _capture_events(root: Path) -> list[dict]:
    return [
        event
        for event in read_events(root)
        if event.get("event_type") == EVENT_CAPTURE_SESSION
    ]


class InvocationClassifierTests(unittest.TestCase):
    def test_claude_print_prompt_is_proxy_eligible(self):
        plan = classify_invocation("claude", ["-p", "summarize this"])
        self.assertEqual(plan.kind, "proxy")
        self.assertEqual(plan.prompt, "summarize this")
        self.assertEqual(plan.mode, "ask")

    def test_claude_model_is_preserved(self):
        plan = classify_invocation(
            "claude", ["--print", "--model", "sonnet", "summarize"]
        )
        self.assertEqual(plan.kind, "proxy")
        self.assertEqual(plan.model, "sonnet")

    def test_claude_structured_output_passes_through_unchanged(self):
        argv = ["-p", "secret prompt", "--output-format", "json"]
        plan = classify_invocation("claude", argv)
        self.assertEqual(plan.kind, "passthrough")
        self.assertEqual(plan.argv, tuple(argv))
        self.assertEqual(plan.reason, "unsupported_option")

    def test_codex_exec_is_safe_auto_and_supports_model(self):
        plan = classify_invocation(
            "codex", ["exec", "--model", "gpt-5.4-mini", "fix tests"]
        )
        self.assertEqual(plan.kind, "proxy")
        self.assertEqual(plan.prompt, "fix tests")
        self.assertEqual(plan.model, "gpt-5.4-mini")
        self.assertEqual(plan.mode, "safe-auto")

    def test_codex_stdin_and_advanced_flags_pass_through(self):
        for argv in (["exec", "-"], ["exec", "--json", "task"]):
            with self.subTest(argv=argv):
                plan = classify_invocation("codex", list(argv))
                self.assertEqual(plan.kind, "passthrough")
                self.assertEqual(plan.argv, tuple(argv))

    def test_copilot_prompt_is_read_only_proxy_eligible(self):
        plan = classify_invocation("copilot", ["-p", "explain this"])
        self.assertEqual(plan.kind, "proxy")
        self.assertEqual(plan.prompt, "explain this")
        self.assertEqual(plan.mode, "ask")

    def test_copilot_explicit_permissions_pass_through(self):
        argv = ["-p", "fix it", "--allow-all-tools"]
        plan = classify_invocation("copilot", argv)
        self.assertEqual(plan.kind, "passthrough")
        self.assertEqual(plan.argv, tuple(argv))

    def test_gemini_approval_modes_map_to_opai_modes(self):
        cases = {
            "plan": "plan",
            "default": "ask",
            "auto_edit": "safe-auto",
            "yolo": "full-auto",
        }
        for approval_mode, expected in cases.items():
            with self.subTest(approval_mode=approval_mode):
                plan = classify_invocation(
                    "gemini",
                    [
                        "-p",
                        "fix the tests",
                        "--approval-mode",
                        approval_mode,
                        "--model",
                        "gemini-2.5-pro",
                    ],
                )
                self.assertEqual(plan.kind, "proxy")
                self.assertEqual(plan.prompt, "fix the tests")
                self.assertEqual(plan.model, "gemini-2.5-pro")
                self.assertEqual(plan.mode, expected)

    def test_gemini_interactive_and_structured_output_pass_through(self):
        for argv in ([], ["--output-format", "json", "-p", "task"]):
            with self.subTest(argv=argv):
                plan = classify_invocation("gemini", argv)
                self.assertEqual(plan.kind, "passthrough")

    def test_interactive_and_management_commands_always_pass_through(self):
        cases = [
            ("claude", []),
            ("claude", ["mcp"]),
            ("codex", ["resume"]),
            ("codex", ["start interactively"]),
            ("copilot", ["login"]),
            ("copilot", ["-i", "start interactively"]),
        ]
        for agent, argv in cases:
            with self.subTest(agent=agent, argv=argv):
                self.assertEqual(classify_invocation(agent, argv).kind, "passthrough")

    def test_agent_card_labels_selective_capture_capability(self):
        card = _client_card(
            {
                "id": "codex",
                "label": "Codex",
                "status": "active",
                "wrapper_installed": True,
                "wrapper_capture_mode": "selective_proxy",
            }
        )
        capture = next(
            metric for metric in card["metrics"] if metric["label"] == "Capture"
        )
        self.assertEqual(capture["value"], "selective proxy")
        self.assertEqual(capture["severity"], "success")

    def test_agent_card_formats_needs_setup_status_for_the_dashboard(self):
        card = _client_card(
            {
                "id": "cursor",
                "label": "Cursor",
                "status": "needs_setup",
                "wrapper_installed": False,
                "wrapper_capture_mode": "missing",
                "global_ready": False,
            }
        )
        self.assertEqual(card["status"], "NEEDS SETUP")

    def test_agent_card_marks_unsupported_global_check_as_not_required(self):
        card = _client_card(
            {
                "id": "cursor",
                "label": "Cursor",
                "status": "active",
                "wrapper_installed": True,
                "wrapper_capture_mode": "selective_proxy",
                "global_required": False,
            }
        )
        global_metric = next(
            metric for metric in card["metrics"] if metric["label"] == "Global"
        )
        self.assertEqual(global_metric["value"], "not required")
        self.assertEqual(global_metric["severity"], "neutral")


class AgentLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_proxy_eligible_invocation_calls_runner_once_and_records_once(self):
        runner = FakeAccountRunner(account_id="claude", cost=0.02)
        result = launch_agent(
            self.root,
            "claude",
            ["-p", "summarize"],
            runner=runner,
        )
        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["launch_kind"], "proxy")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len(_capture_events(self.root)), 1)

    def test_passthrough_is_silent_and_records_no_raw_prompt(self):
        secret = "sk-wrapper-secret-1234567890"
        result = launch_agent(
            self.root,
            "claude",
            ["-p", secret, "--output-format", "json"],
        )
        self.assertEqual(result["status"], "passthrough")
        self.assertEqual(result["exit_code"], PASSTHROUGH_EXIT)
        self.assertNotIn("answer", result)
        persisted = str(read_events(self.root))
        self.assertNotIn(secret, persisted)
        self.assertEqual(_capture_events(self.root)[0]["outcome"], "passthrough")
        self.assertEqual(
            _capture_events(self.root)[0]["reason_code"], "unsupported_option"
        )
        self.assertEqual(_capture_events(self.root)[0]["source"], "wrapper")
        self.assertFalse(_capture_events(self.root)[0]["captured"])

    def test_missing_connected_runner_falls_back_before_proxying(self):
        with mock.patch("opaihub.accounts.runner_for_account", return_value=None):
            result = launch_agent(self.root, "codex", ["exec", "fix tests"])
        self.assertEqual(result["status"], "passthrough")
        self.assertEqual(result["reason"], "account_runner_unavailable")
        self.assertEqual(len(_capture_events(self.root)), 1)

    def test_unsupported_agent_is_passthrough_not_a_crash(self):
        result = launch_agent(self.root, "cursor", ["task"])
        self.assertEqual(result["status"], "passthrough")
        self.assertEqual(result["exit_code"], PASSTHROUGH_EXIT)

    def test_cli_passthrough_is_silent_and_keeps_project_out_of_agent_argv(self):
        from opai.cli import main

        output = StringIO()
        with redirect_stdout(output):
            rc = main(
                [
                    "agent-launch",
                    "--project",
                    str(self.root),
                    "claude",
                    "--",
                    "--output-format",
                    "json",
                    "-p",
                    "private prompt",
                ]
            )
        self.assertEqual(rc, PASSTHROUGH_EXIT)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(_capture_events(self.root)[0]["agent"], "claude")


@unittest.skipUnless(os.name == "nt", "PowerShell wrapper E2E is Windows-specific")
class PowerShellWrapperE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        project = base / "project"
        project.mkdir()
        self.root = make_repo(project)
        self.home = base / "home"
        self.bin = base / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        install_global_integrations(self.root, home=self.home, targets=["shell"])
        self.wrapper = self.home / ".opai" / "bin" / "opai-claude.ps1"
        self.powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not self.powershell:
            self.skipTest("PowerShell is unavailable")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _env(self, agent: str = "claude") -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env["OPAI_WELCOME"] = "0"
        env["OPAI_TEST_WRAPPER"] = str(
            self.home / ".opai" / "bin" / f"opai-{agent}.ps1"
        )
        return env

    def _run_wrapper(
        self, args: list[str], *, agent: str = "claude"
    ) -> subprocess.CompletedProcess[str]:
        quoted = ",".join("'" + item.replace("'", "''") + "'" for item in args)
        command = (
            f"$invokeArgs = @({quoted}); "
            "& $env:OPAI_TEST_WRAPPER @invokeArgs; exit $LASTEXITCODE"
        )
        return subprocess.run(  # nosec B603 - isolated fake wrapper fixture
            [str(self.powershell), "-NoProfile", "-Command", command],
            cwd=self.root,
            env=self._env(agent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def test_unknown_options_passthrough_exact_argv_and_exit_code(self):
        fake = self.bin / "claude.cmd"
        fake.write_text("@echo off\necho RAW:%*\nexit /b 7\n", encoding="utf-8")

        proc = self._run_wrapper(
            ["--output-format", "json", "-p", "private-wrapper-prompt"]
        )

        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertIn("RAW:--output-format json -p private-wrapper-prompt", proc.stdout)
        self.assertNotIn("Using OPai", proc.stdout)
        persisted = str(read_events(self.root))
        self.assertNotIn("private-wrapper-prompt", persisted)
        self.assertEqual(_capture_events(self.root)[0]["outcome"], "passthrough")

    def test_canonical_one_shot_is_proxied_and_accounted_once(self):
        fake = self.bin / "claude.cmd"
        fake.write_text(
            '@echo off\necho {"result":"captured answer","total_cost_usd":0.012}\n',
            encoding="utf-8",
        )
        credentials = self.home / ".claude" / ".credentials.json"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("{}\n", encoding="utf-8")

        proc = self._run_wrapper(["-p", "private-canonical-prompt"])

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "captured answer")
        events = read_events(self.root)
        captures = _capture_events(self.root)
        model_calls = [event for event in events if event["event_type"] == "model_call"]
        self.assertEqual(len(captures), 1)
        self.assertTrue(captures[0]["captured"])
        self.assertEqual(len(model_calls), 1)
        self.assertNotIn("private-canonical-prompt", str(events))

    def test_codex_exec_is_proxied_with_output_file_contract(self):
        helper = self.bin / "fake_codex.py"
        helper.write_text(
            "import pathlib, sys\n"
            "index = sys.argv.index('--output-last-message')\n"
            "pathlib.Path(sys.argv[index + 1]).write_text("
            "'codex captured answer\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake = self.bin / "codex.cmd"
        fake.write_text(
            f'@echo off\n"{sys.executable}" "{helper}" %*\n',
            encoding="utf-8",
        )
        credentials = self.home / ".codex" / "auth.json"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("{}\n", encoding="utf-8")

        proc = self._run_wrapper(["exec", "private-codex-task"], agent="codex")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "codex captured answer")
        self.assertEqual(len(_capture_events(self.root)), 1)
        self.assertTrue(_capture_events(self.root)[0]["captured"])
        self.assertNotIn("private-codex-task", str(read_events(self.root)))

    def test_copilot_prompt_is_proxied_with_plain_output_contract(self):
        fake = self.bin / "copilot.cmd"
        fake.write_text("@echo off\necho copilot captured answer\n", encoding="utf-8")
        credentials = self.home / ".copilot" / "config.json"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("{}\n", encoding="utf-8")

        proc = self._run_wrapper(["-p", "private-copilot-task"], agent="copilot")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "copilot captured answer")
        self.assertEqual(len(_capture_events(self.root)), 1)
        self.assertTrue(_capture_events(self.root)[0]["captured"])
        self.assertNotIn("private-copilot-task", str(read_events(self.root)))


if __name__ == "__main__":
    unittest.main()
