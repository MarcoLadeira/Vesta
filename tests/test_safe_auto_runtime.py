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
from opaihub.gui_pipeline import handle_gui_message
from opaihub.gui_preferences import (
    load_gui_preferences,
    preference_path,
    save_gui_preferences,
    save_mode_preference,
)


def _repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "a@b.c"], cwd=root, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "x"], cwd=root, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, capture_output=True)


class SafeAutoModeResolutionTests(unittest.TestCase):
    def test_old_full_auto_preference_resolves_to_safe_auto(self):
        from opaihub.autonomy import resolve_mode

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            path = preference_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"schema_version": 1, "default_mode": "full-auto"}),
                encoding="utf-8",
            )
            prefs = load_gui_preferences(root)
            resolved = resolve_mode(root)

        self.assertEqual(prefs["default_mode"], "full-auto")
        self.assertFalse(prefs["full_auto_pinned"])
        self.assertEqual(resolved["requested_mode"], "full-auto")
        self.assertEqual(resolved["effective_mode"], "safe-auto")
        self.assertIn("reset", resolved["warning"].lower())

    def test_explicit_full_auto_pin_keeps_full_auto(self):
        from opaihub.autonomy import resolve_mode

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            prefs = save_mode_preference(root, "full-auto", confirm_full_auto=True)
            resolved = resolve_mode(root)

        self.assertEqual(prefs["default_mode"], "full-auto")
        self.assertTrue(prefs["full_auto_pinned"])
        self.assertEqual(resolved["effective_mode"], "full-auto")
        self.assertTrue(resolved["full_auto_pinned"])

    def test_saving_full_auto_without_confirmation_falls_back_to_safe_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            prefs = save_mode_preference(root, "full-auto")

        self.assertEqual(prefs["default_mode"], "safe-auto")
        self.assertFalse(prefs["full_auto_pinned"])
        self.assertEqual(prefs["last_requested_mode"], "full-auto")

    def test_gui_once_exposes_effective_mode_and_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            save_gui_preferences(root, {"default_mode": "full-auto"})
            summary = run_once(root)

        self.assertEqual(summary["requested_mode"], "full-auto")
        self.assertEqual(summary["effective_mode"], "safe-auto")
        self.assertFalse(summary["full_auto_pinned"])
        self.assertIn("Full Auto", summary["mode_warning"])
        self.assertIn("autonomy_policy", summary)

    def test_stale_full_auto_warning_does_not_block_safe_auto_message(self):
        class FakeRunner:
            model = "sonnet"

            def available(self):
                return True

            def complete(self, *args, **kwargs):
                return {"text": "answered", "cost": 0.0}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            save_gui_preferences(root, {"default_mode": "full-auto"})
            result = handle_gui_message(
                root,
                "summarize this safely",
                model_id="account:claude:sonnet",
                account_runner=FakeRunner(),
            )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["effective_mode"], "safe-auto")
        self.assertTrue(any("Full Auto" in w["reason"] for w in result["warnings"]))

    def test_cli_autonomy_inspect_reports_effective_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            save_gui_preferences(root, {"default_mode": "full-auto"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["autonomy", "inspect", "--project", str(root)])
            data = json.loads(buf.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(data["requested_mode"], "full-auto")
        self.assertEqual(data["effective_mode"], "safe-auto")
        self.assertIn("next_action", data)


class SafeAutoDecisionTests(unittest.TestCase):
    def test_evaluate_action_blocks_destructive_and_deploy_commands(self):
        from opaihub.autonomy import evaluate_action

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            destructive = evaluate_action(root, "safe-auto", command="git reset --hard")
            deploy = evaluate_action(root, "safe-auto", command="wrangler deploy")

        self.assertEqual(destructive["decision"], "deny")
        self.assertTrue(destructive["blocked"])
        self.assertEqual(destructive["risk"], "destructive")
        self.assertIn(deploy["decision"], {"confirm", "deny"})
        self.assertTrue(deploy["requires_confirmation"] or deploy["blocked"])
        self.assertEqual(deploy["risk"], "deploy")

    def test_safe_auto_blocks_outside_project_write(self):
        from opaihub.autonomy import evaluate_action

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            outside = Path(tmp).parent / "outside.txt"
            decision = evaluate_action(
                root,
                "safe-auto",
                command=f"python -c \"open(r'{outside}', 'w').write('x')\"",
                mutates=True,
            )

        self.assertEqual(decision["risk"], "outside_project")
        self.assertTrue(decision["blocked"] or decision["requires_confirmation"])

    def test_paid_account_call_requires_confirmation_in_safe_auto(self):
        from opaihub.autonomy import evaluate_action

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            decision = evaluate_action(
                root,
                "safe-auto",
                model_id="account:claude:sonnet",
                mutates=False,
            )

        self.assertEqual(decision["risk"], "paid")
        self.assertTrue(decision["requires_confirmation"])


class SafeAutoPipelineTests(unittest.TestCase):
    def test_edit_capable_account_run_creates_checkpoint_and_run_state(self):
        from opaihub.checkpoints import latest_checkpoint
        from opaihub.run_state import latest_run

        class FakeRunner:
            model = "sonnet"

            def available(self):
                return True

            def complete(self, *args, **kwargs):
                return {"text": "done", "cost": 0.0}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = handle_gui_message(
                root,
                "edit the docs safely",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=FakeRunner(),
            )
            checkpoint = latest_checkpoint(root)
            run = latest_run(root)
            blob = json.dumps({"checkpoint": checkpoint, "run": run, "result": result})

        self.assertEqual(result["effective_mode"], "safe-auto")
        self.assertTrue(result["checkpoint_id"])
        self.assertTrue(result["run_id"])
        self.assertEqual(checkpoint["id"], result["checkpoint_id"])
        self.assertEqual(run["id"], result["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertNotIn("edit the docs safely", blob)
        self.assertNotIn("SECRET", blob)

    def test_blocked_request_records_blocked_run_without_model_call(self):
        from opaihub.run_state import latest_run

        class FakeRunner:
            model = "sonnet"

            def available(self):
                return True

            def complete(self, *args, **kwargs):
                raise AssertionError("blocked request must not call model")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = handle_gui_message(
                root,
                "delete the repo with rm -rf",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=FakeRunner(),
            )
            run = latest_run(root)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["decision"]["decision"], "deny")


class CancellableRunnerTests(unittest.TestCase):
    def test_account_runner_cancel_event_stops_process_cleanly(self):
        import threading
        import time

        from opaihub import accounts
        from opaihub.accounts import AccountRunner

        class FakeProc:
            def __init__(self):
                self.stdout = ""
                self.stderr = ""
                self.returncode = None
                self.terminated = False

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                time.sleep(0.05)
                if self.terminated:
                    self.returncode = -15
                    return ("", "stopped")
                raise accounts.subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.terminated = True

        fake = FakeProc()
        cancel = threading.Event()
        cancel.set()
        runner = AccountRunner("claude", "/bin/claude")
        with mock.patch.object(accounts.subprocess, "Popen", return_value=fake):
            result = runner.complete("hi", cancel_event=cancel, timeout=1)

        self.assertTrue(result["stopped"])
        self.assertTrue(fake.terminated)


if __name__ == "__main__":
    unittest.main()
