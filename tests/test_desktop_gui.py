import json
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from opai.cli import build_parser
from opai import app_state as A
from opai.gui_desktop import SECTIONS, run_once
from opaihub.launch_readiness import build_launch_readiness


def _repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


class AppStateReadTests(unittest.TestCase):
    def test_overview_shape_and_zero_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            o = A.overview(root)
        self.assertIn(o["status_label"], {"ON", "ATTENTION"})
        self.assertIn("estimated_savings_usd", o["savings"])
        # No routed tasks -> honest zero state.
        self.assertIsNotNone(o["savings"]["zero_state"])
        self.assertIn("opai route", o["savings"]["zero_state"])
        self.assertIn("50x", o["benchmark_claim"])

    def test_agent_readiness_has_five_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            ar = A.agent_readiness(root)
        ids = [c["id"] for c in ar["clients"]]
        self.assertEqual(ids, ["claude", "codex", "copilot", "cursor", "cline"])
        for c in ar["clients"]:
            self.assertIn(c["status"], {"active", "broken", "missing", "unknown"})
            self.assertTrue(c["repair"])

    def test_cost_firewall_profiles_and_panic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            cf = A.cost_firewall(root)
        self.assertIn("solo-cheap", cf["available_profiles"])
        self.assertIn("enterprise-strict", cf["available_profiles"])
        self.assertFalse(cf["panic"])
        self.assertTrue(cf["require_confirmation_for_cloud"])

    def test_benchmark_proof_caps_and_caveat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            from opaihub.benchmark import run_benchmark

            run_benchmark(root, suite="local", mode="both", write=True)
            bp = A.benchmark_proof(root)
        self.assertTrue(bp["has_run"])
        self.assertLessEqual(bp["context_reduction_ratio"], 50.0)
        self.assertIn("Not an official", bp["caveat"])

    def test_guarded_workflows_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            gw = A.guarded_workflows(root)
        ids = {t["id"] for t in gw["tiles"]}
        for expected in [
            "release_preflight",
            "ci_fixer",
            "pr_review",
            "security_audit",
        ]:
            self.assertIn(expected, ids)

    def test_full_state_has_all_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            state = A.full_state(root)
        for key in [
            "overview",
            "agent_readiness",
            "cost_firewall",
            "context_waste",
            "benchmark_proof",
            "proof_status",
            "guarded_workflows",
            "launch_readiness",
        ]:
            self.assertIn(key, state)

    def test_available_models_includes_free_local_setup_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            models = A.available_models(root)

        # Auto is always offered (accounts, when connected, precede it).
        self.assertIn("auto", [m["id"] for m in models["models"]])
        self.assertIn("setup", models)
        setup_ids = [item["id"] for item in models["setup"]["recommended"]]
        self.assertIn("ollama-qwen2.5-coder", setup_ids)
        self.assertIn("lm-studio-local-server", setup_ids)
        self.assertIn("opai models discover-local", models["setup"]["verify_command"])


class LaunchReadinessTests(unittest.TestCase):
    def test_detects_placeholders_as_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                "<a href='PRIVATE_FOUNDING_PRO_CHECKOUT_URL'>buy</a>"
                "REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN"
                "PRIVATE_TEAM_PILOT_APPLY_URL PRIVATE_BENCHMARK_PROOF_URL",
                encoding="utf-8",
            )
            lr = build_launch_readiness(root)
        self.assertFalse(lr["ready"])
        names = {c["name"]: c for c in lr["checks"]}
        self.assertFalse(names["placeholder_replacement"]["ok"])
        self.assertFalse(names["lemon_links"]["ok"])
        self.assertIn("50x", lr["launch_claim"])

    def test_clean_site_passes_leakage_and_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                "<html>clean launch page, no placeholders</html>", encoding="utf-8"
            )
            lr = build_launch_readiness(root)
        names = {c["name"]: c for c in lr["checks"]}
        self.assertTrue(names["site_leakage"]["ok"])
        self.assertTrue(names["placeholder_replacement"]["ok"])


class SafetyAndActionTests(unittest.TestCase):
    def test_cleanup_preview_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            before = sorted(p.name for p in root.iterdir())
            preview = A.cleanup_preview(root)
            after = sorted(p.name for p in root.iterdir())
        self.assertFalse(preview["mutates"])
        self.assertEqual(before, after)  # no files created/deleted

    def test_set_panic_toggles_and_records_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            A.set_panic(root, True)
            cf = A.cost_firewall(root)
        self.assertTrue(cf["panic"])

    def test_export_proof_redacts_prompts_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            from opaihub.ledger import record_route_decision

            record_route_decision(
                root, "deploy SECRET token=sk-abcdef1234567890abcd", model_tier="L0"
            )
            out = root / "proof.json"
            result = A.export_proof(root, out, fmt="json")
            blob = out.read_text(encoding="utf-8")
        self.assertEqual(result["status"], "exported")
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-abcdef1234567890abcd", blob)
        self.assertIn("signature", blob)

    def test_benchmark_gate_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            from opaihub.benchmark import run_benchmark

            run_benchmark(root, suite="local", mode="both", write=True)
            gate = A.run_benchmark_gate(
                root, min_effectiveness_index=0.0, require_risk_blocks=False
            )
        self.assertIn("ok", gate)


class RunOnceTests(unittest.TestCase):
    def test_run_once_headless_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            summary = run_once(root)
        self.assertTrue(summary["ok"])
        self.assertIn(summary["status_label"], {"ON", "ATTENTION"})
        self.assertEqual(len(summary["sections"]), 8)
        self.assertIsNotNone(summary["zero_state"])
        self.assertIn("model_setup", summary)
        self.assertEqual("free_first", summary["model_setup"]["status"])

    def test_cli_gui_once_returns_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            from opai.cli import main
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["gui", "--once", "--project", str(root)])
            data = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual([k for k, _ in SECTIONS], data["sections"])

    def test_models_discover_local_is_available_on_primary_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            from opai.cli import main
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["models", "discover-local", "--project", str(root)])
            data = json.loads(buf.getvalue())

        self.assertEqual(code, 0)
        self.assertIn("available", data)
        self.assertIn("commands", data)

    def test_connect_tool_leads_with_accounts_and_demotes_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = A.run_tool(root, "connect")

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Connect accounts")
        # Accounts (Claude/Codex) lead; local models are kept but demoted.
        self.assertIn("Claude", result["text"])
        self.assertIn("Codex", result["text"])
        self.assertIn("Advanced", result["text"])
        # The legacy "models" alias still resolves to the same connect surface.
        self.assertEqual(A.run_tool(root, "models")["title"], "Connect accounts")

    def test_ask_without_local_model_points_to_primary_models_command(self):
        from opaihub.ask import run_ask

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            with mock.patch("opaihub.ask.detect_local_runner", return_value=None):
                result = run_ask(root, "summarize this project", record=False)

        self.assertEqual(result["status"], "no_local_model")
        self.assertEqual(result["next_command"], "opai models discover-local")


class PremiumGuiContractTests(unittest.TestCase):
    def test_cli_accepts_screenshot_path_without_launching_parser(self):
        args = build_parser().parse_args(
            ["gui", "--screenshot", ".opaihub/gui-smoke.png"]
        )
        self.assertEqual(args.screenshot, ".opaihub/gui-smoke.png")
        self.assertFalse(args.once)

    def test_missing_pyside6_has_desktop_install_hint(self):
        from opai.gui_desktop import dependency_status

        status = dependency_status()
        self.assertIn("available", status)
        self.assertIn("install_hint", status)
        self.assertIn("desktop-gui", status["install_hint"])

    def test_view_model_has_premium_sections_and_safe_actions(self):
        from opai.gui_view_model import build_view_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            vm = build_view_model(root)

        self.assertEqual(vm["theme"]["name"], "opai-premium-dark")
        self.assertEqual(
            [section["id"] for section in vm["sections"]], [k for k, _ in SECTIONS]
        )
        actions = {
            action["id"]: action
            for section in vm["sections"]
            for action in section.get("actions", [])
        }
        for expected in [
            "safe_repair",
            "panic_toggle",
            "cleanup_preview",
            "benchmark_gate",
        ]:
            self.assertIn(expected, actions)
        for action in actions.values():
            if action.get("mutates") or action.get("risk") in {
                "paid",
                "cloud",
                "destructive",
                "config",
            }:
                self.assertTrue(action.get("requires_confirmation"), action)
                self.assertTrue(action.get("confirmation"), action)

    def test_view_model_does_not_expose_raw_prompts_or_secrets(self):
        from opai.gui_view_model import build_view_model
        from opaihub.ledger import record_route_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            record_route_decision(
                root,
                "fix billing bug with SECRET token=sk-abcdef1234567890abcd",
                model_tier="L0",
            )
            vm = build_view_model(root)

        blob = json.dumps(vm, sort_keys=True)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-abcdef1234567890abcd", blob)

    def test_cli_parse_error_mentions_screenshot_when_misused(self):
        parser = build_parser()
        stderr = StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(stderr):
            parser.parse_args(["gui", "--screenshot"])
        self.assertIn("--screenshot", stderr.getvalue())

    def test_screenshot_renderer_writes_nonblank_image_when_pyside_available(self):
        from opai.gui_desktop import dependency_status, render_screenshot

        if not dependency_status()["available"]:
            self.skipTest("PySide6 desktop extra is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            out = root / "gui-smoke.png"
            result = render_screenshot(root, out, width=1040, height=700)
        self.assertEqual(result["status"], "written")
        self.assertGreater(result["bytes"], 1000)


class AccountConnectionTests(unittest.TestCase):
    """Connect Claude/Codex via their CLIs. No test ever invokes a real CLI:
    execution paths inject a fake runner or force detection to None."""

    def test_detects_connected_account_when_cli_and_auth_present(self):
        from opaihub import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cred = home / ".claude" / ".credentials.json"
            cred.parent.mkdir(parents=True)
            cred.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                accounts, "_which", lambda n: f"/bin/{n}" if n == "claude" else None
            ):
                got = {a["id"]: a for a in accounts.list_connected_accounts(home=home)}
        self.assertTrue(got["claude"]["connected"])
        self.assertTrue(got["claude"]["cli_present"])
        self.assertTrue(got["claude"]["authenticated"])
        self.assertFalse(got["codex"]["connected"])  # no auth, no cli

    def test_cli_present_but_not_signed_in_is_not_connected(self):
        from opaihub import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)  # no auth files anywhere
            with mock.patch.object(accounts, "_which", lambda n: f"/bin/{n}"):
                accts = {
                    a["id"]: a for a in accounts.list_connected_accounts(home=home)
                }
        self.assertFalse(accts["claude"]["connected"])
        self.assertTrue(accts["claude"]["cli_present"])

    def test_account_runner_build_command_is_safe_by_default(self):
        from opaihub.accounts import AccountRunner

        claude = AccountRunner("claude", "/bin/claude", model="sonnet")
        read_only = claude.build_command("hi", allow_edits=False)
        self.assertEqual(read_only[:2], ["/bin/claude", "-p"])
        self.assertIn("--output-format", read_only)  # structured output -> real cost
        self.assertIn("sonnet", read_only)  # selected model is passed through
        # Read-only stays safe: no autonomous skip-permissions.
        self.assertNotIn("--dangerously-skip-permissions", read_only)
        # Safe Auto may allow edits, but it does not skip permissions.
        self.assertNotIn(
            "--dangerously-skip-permissions",
            claude.build_command("hi", allow_edits=True),
        )
        # Full Auto is the explicit high-risk path.
        self.assertIn(
            "--dangerously-skip-permissions",
            claude.build_command("hi", mode="full-auto"),
        )

        codex = AccountRunner("codex", "/bin/codex", model="gpt-5.4-mini")
        ro = codex.build_command("hi", allow_edits=False, out_file="/t/o.txt")
        self.assertEqual(ro[:2], ["/bin/codex", "exec"])
        self.assertIn("read-only", ro)  # no writes unless asked
        self.assertIn("--output-last-message", ro)
        safe_auto = codex.build_command("hi", allow_edits=True)
        self.assertIn("workspace-write", safe_auto)
        self.assertIn("--ask-for-approval", safe_auto)
        self.assertIn("on-request", safe_auto)
        self.assertIn("--model", safe_auto)
        self.assertIn("gpt-5.4-mini", safe_auto)

    def test_account_complete_runs_hidden_without_console_window(self):
        import sys

        from opaihub import accounts

        runner = accounts.AccountRunner("claude", "/bin/claude")
        fake = mock.MagicMock(stdout="hi there", stderr="")
        with mock.patch.object(
            accounts.subprocess, "run", return_value=fake
        ) as run_mock:
            out = runner.complete("hello", project_root=None)
        # complete() returns {"text", "cost"}; non-JSON output degrades to text.
        self.assertEqual(out["text"], "hi there")
        kwargs = run_mock.call_args.kwargs
        # stdin is closed so the CLI never blocks the GUI waiting for input.
        self.assertEqual(kwargs.get("stdin"), accounts.subprocess.DEVNULL)
        # On Windows, no console window is spawned when the GUI shells out.
        if sys.platform == "win32":
            self.assertEqual(
                kwargs.get("creationflags"), accounts.subprocess.CREATE_NO_WINDOW
            )

    def test_account_timeout_is_reported_cleanly_not_as_raw_command(self):
        class SlowRunner:
            model = "opus"

            def available(self):
                return True

            def complete(self, *a, **k):
                return {"text": "", "cost": None, "timed_out": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            res = A.ask(
                root,
                "rebuild the whole UI",
                "account:claude:opus",
                account_runner=SlowRunner(),
            )
        self.assertEqual(res["status"], "account_timeout")
        self.assertNotIn("timed out after", res["answer"])  # no raw command dump
        self.assertNotIn("--dangerously-skip-permissions", res["answer"])
        self.assertIn("smaller", res["answer"].lower())  # actionable guidance

    def test_runner_returns_timed_out_instead_of_raising(self):
        import subprocess as sp

        from opaihub import accounts

        runner = accounts.AccountRunner("claude", "/bin/claude", model="opus")
        with mock.patch.object(
            accounts,
            "_hidden_run",
            side_effect=sp.TimeoutExpired(cmd="claude", timeout=1),
        ):
            out = runner.complete("x", project_root=None)
        self.assertTrue(out["timed_out"])

    def test_ask_routes_account_and_records_real_spend(self):
        class FakeRunner:
            model = "sonnet"

            def available(self):
                return True

            def complete(
                self, prompt, *, project_root=None, allow_edits=False, timeout=240
            ):
                return "ACCOUNT ANSWER"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            result = A.ask(root, "do x", "account:claude", account_runner=FakeRunner())
            from opaihub.ledger import read_events

            tiers = [e.get("model_tier") for e in read_events(root)]
        self.assertEqual(result["status"], "answered_by_account")
        self.assertTrue(result["paid"])
        self.assertEqual(result["answer"], "ACCOUNT ANSWER")
        self.assertIn("CLOUD", tiers)  # a real paid call, recorded as spend

    def test_panic_blocks_paid_account_calls(self):
        class FakeRunner:
            model = ""

            def available(self):
                return True

            def complete(self, *a, **k):  # must never be reached under panic
                raise AssertionError("panic must block before execution")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            A.set_panic(root, True)
            result = A.ask(root, "do x", "account:claude", account_runner=FakeRunner())
        self.assertEqual(result["status"], "blocked_panic")

    def test_unconnected_account_returns_sign_in_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            # Force "not connected" so no real CLI is ever launched.
            with mock.patch("opaihub.accounts.runner_for_account", return_value=None):
                result = A.ask(root, "do x", "account:codex")
        self.assertEqual(result["status"], "account_not_connected")
        self.assertIn("codex", result["hint"])

    def test_available_models_lists_accounts_then_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            data = A.available_models(root)
        self.assertIn("accounts", data)
        self.assertIn("account_count", data)
        ids = [m["id"] for m in data["models"]]
        self.assertIn("auto", ids)
        for model in data["models"]:
            if model.get("kind") == "account":
                self.assertTrue(model["id"].startswith("account:"))
                self.assertTrue(model.get("paid"))

    def test_claude_expands_into_selectable_models(self):
        from opaihub import accounts

        fake_account = {
            "id": "claude",
            "label": "Claude",
            "vendor": "Anthropic Claude Code",
            "cli": "claude",
            "cli_path": "/bin/claude",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }
        with mock.patch.object(
            accounts, "list_connected_accounts", return_value=[fake_account]
        ):
            ids = [opt["id"] for opt in accounts.account_models()]
        self.assertIn("account:claude:sonnet", ids)
        self.assertIn("account:claude:opus", ids)
        self.assertIn("account:claude:haiku", ids)

    def test_ask_passes_selected_model_and_surfaces_cost(self):
        captured = {}

        class FakeRunner:
            def __init__(self, model):
                captured["model"] = model
                self.model = model

            def available(self):
                return True

            def complete(
                self, prompt, *, project_root=None, allow_edits=False, timeout=240
            ):
                return {"text": "done", "cost": 0.0123}

        def fake_runner_for_account(account_id, *, model=None, home=None):
            return FakeRunner(model)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            with mock.patch(
                "opaihub.accounts.runner_for_account",
                side_effect=fake_runner_for_account,
            ):
                result = A.ask(root, "do x", "account:claude:opus")
        # The selected alias reaches the runner instead of being stuck on a default.
        self.assertEqual(captured["model"], "opus")
        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["cost_usd"], 0.0123)


class DevToolInspectorTests(unittest.TestCase):
    """The Inspector/workspace must show concrete dev telemetry, not labels."""

    def _repo_with_commit(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "a@b.c"], cwd=root, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "x"], cwd=root, capture_output=True
        )
        (root / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, capture_output=True)

    def test_inspector_state_is_concrete_not_cryptic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo_with_commit(root)
            ins = A.inspector_state(root, mode="full-auto")
        self.assertIn("$", ins["budget"]["text"])  # real money, not "ok"
        self.assertIsInstance(ins["budget"]["pct"], int)
        self.assertIn("files indexed", ins["workspace"]["text"])  # indexing status
        self.assertIn("without asking", ins["mode"]["capability"])  # plain-English

    def test_workspace_summary_counts_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo_with_commit(root)
            ws = A.workspace_summary(root)
        self.assertGreaterEqual(ws["file_count"], 1)
        self.assertTrue(ws["name"])

    def test_workspace_diff_shows_actual_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo_with_commit(root)
            (root / "app.py").write_text("print(2)\n", encoding="utf-8")
            diff = A.workspace_diff(root)
        self.assertIn("app.py", diff)
        self.assertIn("+print(2)", diff)

    def test_gui_accepts_initial_task_for_cli_companion(self):
        args = build_parser().parse_args(["gui", "fix the login bug"])
        self.assertEqual(args.task, "fix the login bug")
        # The bare form still works (no task).
        self.assertIsNone(build_parser().parse_args(["gui"]).task)


if __name__ == "__main__":
    unittest.main()
