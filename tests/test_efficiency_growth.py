import subprocess
import tempfile
import unittest
from pathlib import Path

from vestahub.context_pack import build_context_pack
from vestahub.metrics import build_local_metrics
from vestahub.ledger import record_route_decision
from vestahub.runs import explain_route, recent_runs, record_run, render_why_markdown
from vestahub.share import build_savings_card, render_share_markdown
from vestahub.test_select import likely_tests_for, select_tests


def _repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "def test_add():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=x", "commit", "-qm", "init"],
        cwd=root,
        check=True,
        capture_output=True,
    )


class TestSelectionTests(unittest.TestCase):
    def test_maps_source_to_existing_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            tests = likely_tests_for("app.py", root)
        self.assertIn("tests/test_app.py", tests)

    def test_select_tests_targets_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            (root / "app.py").write_text("def add(a, b):\n    return a + b + 0\n")
            selection = select_tests(root)
        self.assertIn("app.py", selection["changed_files"])
        self.assertIn("tests/test_app.py", selection["selected_tests"])
        self.assertIn("pytest", selection["targeted_command"])

    def test_no_changes_means_no_targeted_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            selection = select_tests(root)
        self.assertEqual(selection["selected_tests"], [])
        self.assertIsNone(selection["targeted_command"])


class ContextPackTests(unittest.TestCase):
    def test_pack_includes_changed_file_and_adjacent_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            (root / "app.py").write_text("def add(a, b):\n    return a + b + 1\n")
            pack = build_context_pack(root)
        paths = [f["path"] for f in pack["files"]]
        self.assertIn("app.py", paths)
        self.assertIn("tests/test_app.py", pack["adjacent_tests"])
        self.assertLessEqual(pack["used_chars"], pack["char_budget"])

    def test_pack_redacts_secrets_in_snippets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            (root / "config.py").write_text(
                "api_key = 'sk-deadbeefdeadbeef1234567890'\n", encoding="utf-8"
            )
            pack = build_context_pack(root, char_budget=10000)
        blob = str(pack["files"])
        self.assertNotIn("sk-deadbeefdeadbeef1234567890", blob)

    def test_pack_excludes_vesta_managed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            # Untracked Vesta-managed files must not waste the pack budget.
            (root / "AGENTS.md").write_text("managed\n", encoding="utf-8")
            (root / ".cursor" / "rules").mkdir(parents=True)
            (root / ".cursor" / "rules" / "vesta.mdc").write_text(
                "x\n", encoding="utf-8"
            )
            (root / "real_change.py").write_text("y = 2\n", encoding="utf-8")
            pack = build_context_pack(root)
        paths = [f["path"] for f in pack["files"]]
        self.assertIn("real_change.py", paths)
        self.assertNotIn("AGENTS.md", paths)
        self.assertFalse(any(".cursor" in p for p in paths))

    def test_budget_truncates_large_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            (root / "big.py").write_text("x = 1\n" * 5000, encoding="utf-8")
            pack = build_context_pack(root, char_budget=50)
        self.assertTrue(pack["truncated"])


class RunHistoryTests(unittest.TestCase):
    def test_record_and_read_runs_without_raw_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = {
                "task": "deploy production now",
                "workflow": "release_prepare",
                "model_tier": "L0",
                "policy_profile": "solo-balanced",
                "policy_decision": "allow",
                "evidence_cache_hit": True,
                "estimated_cost_usd": 0.0,
            }
            record_run(root, decision)
            runs = recent_runs(root)
        self.assertEqual(len(runs), 1)
        self.assertNotIn("deploy", str(runs))
        self.assertTrue(runs[0]["cache_hit"])

    def test_explain_route_gives_reasons_and_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo(root)
            explanation = explain_route(root, "show git status and diff")
        self.assertEqual(explanation["model_tier"], "L0")
        self.assertGreater(explanation["estimated_savings_usd"], 0.0)
        self.assertTrue(explanation["reasons"])
        self.assertIn("Vesta - Why this route", render_why_markdown(explanation))


class ShareCardTests(unittest.TestCase):
    def test_card_reflects_recorded_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "fix bug", model_tier="L0")
            card = build_savings_card(root)
        self.assertTrue(card["has_data"])
        self.assertIn("shields.io", card["badge"]["shields_url"])
        self.assertIn("<svg", card["badge"]["svg"])
        self.assertIn("Vesta saved", render_share_markdown(card))

    def test_empty_card_prompts_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = build_savings_card(Path(tmp))
        self.assertFalse(card["has_data"])


class MetricsTests(unittest.TestCase):
    def test_metrics_report_cache_rate_and_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "fix bug", model_tier="L0")
            record_run(
                root,
                {"task": "fix bug", "model_tier": "L0", "evidence_cache_hit": True},
            )
            record_run(
                root, {"task": "other", "model_tier": "L1", "evidence_cache_hit": False}
            )
            metrics = build_local_metrics(root)
        self.assertEqual(metrics["route_runs_recorded"], 2)
        self.assertEqual(metrics["evidence_cache_hit_rate"], 0.5)
        self.assertGreaterEqual(metrics["cloud_escalations_avoided"], 1)


if __name__ == "__main__":
    unittest.main()
