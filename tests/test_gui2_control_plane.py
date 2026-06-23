from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from opai.cli import main
from opai.gui_desktop import run_once
from opaihub.accounts import AccountRunner, account_models
from opaihub.gui_pipeline import handle_gui_message
from opaihub.gui_preferences import (
    DEFAULT_MODE,
    load_gui_preferences,
    preference_path,
    save_gui_preferences,
)
from opaihub.intent_router import route_intents
from opaihub.ledger import read_events


def _repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


class Gui2ModelAndPreferenceTests(unittest.TestCase):
    def test_codex_expands_into_selectable_current_models(self):
        from opaihub import accounts

        fake_account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }
        with mock.patch.object(
            accounts, "list_connected_accounts", return_value=[fake_account]
        ):
            ids = [option["id"] for option in account_models()]

        self.assertIn("account:codex:gpt-5.5", ids)
        self.assertIn("account:codex:gpt-5.4", ids)
        self.assertIn("account:codex:gpt-5.4-mini", ids)
        self.assertIn("account:codex:gpt-5.3-codex-spark", ids)

    def test_codex_selected_model_reaches_exec_command(self):
        runner = AccountRunner("codex", "/bin/codex", model="gpt-5.4-mini")

        cmd = runner.build_command("fix tests", mode="safe-auto")

        self.assertIn("--model", cmd)
        self.assertIn("gpt-5.4-mini", cmd)
        self.assertIn("workspace-write", cmd)
        self.assertIn("on-request", cmd)

    def test_preferences_store_only_safe_gui_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            prefs = save_gui_preferences(
                root,
                {
                    "default_model": "account:codex:gpt-5.4-mini",
                    "default_mode": "safe-auto",
                    "raw_prompt": "SECRET token=sk-abcdef1234567890abcd",
                },
            )
            loaded = load_gui_preferences(root)
            blob = preference_path(root).read_text(encoding="utf-8")

        self.assertEqual(prefs["default_model"], "account:codex:gpt-5.4-mini")
        self.assertEqual(loaded["default_mode"], "safe-auto")
        self.assertNotIn("raw_prompt", loaded)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-abcdef1234567890abcd", blob)

    def test_primary_cli_lists_models_and_sets_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["models", "list", "--project", str(root)])
            listed = json.loads(buf.getvalue())
            buf = io.StringIO()
            with redirect_stdout(buf):
                set_code = main(
                    [
                        "models",
                        "set-default",
                        "account:codex:gpt-5.4-mini",
                        "--project",
                        str(root),
                    ]
                )
            updated = json.loads(buf.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(set_code, 0)
        self.assertIn("groups", listed)
        self.assertEqual(
            updated["preferences"]["default_model"], "account:codex:gpt-5.4-mini"
        )


class Gui2ModeAndAutomationTests(unittest.TestCase):
    def test_mode_mappings_keep_dangerous_claude_only_for_full_auto(self):
        runner = AccountRunner("claude", "/bin/claude", model="sonnet")

        safe = runner.build_command("edit safely", mode="safe-auto")
        full = runner.build_command("edit fully", mode="full-auto")

        self.assertNotIn("--dangerously-skip-permissions", safe)
        self.assertIn("--dangerously-skip-permissions", full)

    def test_intent_router_auto_calls_safe_read_only_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            trace = route_intents(
                root, "fix the failing test cheaply", mode="safe-auto"
            )

        ids = [item["id"] for item in trace]
        self.assertIn("model_recommend", ids)
        self.assertIn("budget_gate", ids)
        self.assertIn("test_select", ids)
        for item in trace:
            self.assertTrue(item["auto_allowed"])
            self.assertFalse(item["mutates"])

    def test_safe_auto_blocks_destructive_intent_before_account_call(self):
        class FakeRunner:
            model = "gpt-5.5"

            def available(self):
                return True

            def complete(self, *args, **kwargs):
                raise AssertionError(
                    "dangerous task should be blocked before model call"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = handle_gui_message(
                root,
                "delete the repository with rm -rf",
                model_id="account:codex:gpt-5.5",
                mode="safe-auto",
                account_runner=FakeRunner(),
            )

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["receipt"]["confidence"], "blocked")

    def test_gui_message_records_tool_trace_and_savings_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = handle_gui_message(
                root,
                "summarize git status and changed files",
                model_id="auto",
                mode="safe-auto",
            )
            events = read_events(root)

        self.assertIn("tool_trace", result)
        self.assertIn("receipt", result)
        self.assertGreater(result["receipt"]["estimated_baseline_usd"], 0)
        self.assertIn(
            result["status"], {"answered", "needs_model", "needs_confirmation"}
        )
        self.assertTrue(events)

    def test_gui_once_exposes_mode_defaults_and_last_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            summary = run_once(root)

        self.assertEqual(summary["mode"], DEFAULT_MODE)
        self.assertIn("available_models", summary)
        self.assertIn("auto_policy", summary)
        self.assertIn("last_savings_receipt", summary)


if __name__ == "__main__":
    unittest.main()
