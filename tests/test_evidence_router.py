import tempfile
import unittest
from pathlib import Path

from opaihub.evidence import collect_evidence
from opaihub.loader import registry_items
from opaihub.router import route_task


class EvidenceRouterTests(unittest.TestCase):
    def test_collect_evidence_detects_project_markers_without_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\n", encoding="utf-8"
            )
            (root / "tests").mkdir()

            evidence = collect_evidence(root, "fix tests")

        self.assertFalse(evidence["ai_used"])
        self.assertIn("pyproject.toml", evidence["markers"])
        self.assertIn("python -m unittest discover -s tests", evidence["test_commands"])
        self.assertIn("cache_key", evidence)

    def test_collect_evidence_skips_git_diff_when_project_is_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            evidence = collect_evidence(root, "inspect project")

        self.assertFalse(evidence["git"]["is_repo"])
        self.assertFalse(evidence["git"]["changed_files"]["executed"])
        self.assertEqual(
            evidence["git"]["changed_files"]["output_tail"],
            "skipped: not a git repository",
        )

    def test_route_failing_tests_uses_test_debug_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()

            decision = route_task(root, "fix failing tests in auth")

        self.assertEqual(decision["workflow"], "test_failure_debug")
        self.assertEqual(decision["model_tier"], "L0")
        self.assertFalse(decision["requires_confirmation"])
        self.assertIn("run targeted tests first", decision["next_actions"])

    def test_route_deploy_requires_confirmation_before_strong_ai_or_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = route_task(Path(tmp), "deploy production release")

        self.assertEqual(decision["workflow"], "release_prepare")
        self.assertEqual(decision["model_tier"], "L3")
        self.assertTrue(decision["requires_confirmation"])
        self.assertIn("ask before deploy/cloud action", decision["safety_gates"])

    def test_router_returns_registry_backed_workflows(self):
        workflow_ids = {
            workflow["id"] for workflow in registry_items("workflows", Path.cwd())
        }
        tasks = [
            "show git status",
            "fix failing tests",
            "security review",
            "deploy production release",
            "add a feature",
        ]

        for task in tasks:
            with self.subTest(task=task):
                decision = route_task(Path.cwd(), task)
                self.assertIn(decision["workflow"], workflow_ids)


if __name__ == "__main__":
    unittest.main()
