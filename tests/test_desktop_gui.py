import json
import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
