import tempfile
import unittest
from pathlib import Path

from opcoding.cost import route_task
from opcoding.scanner import scan_project


class CoreSmokeTests(unittest.TestCase):
    def test_node_scan_detects_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"scripts":{"test":"vitest","build":"vite build"},"dependencies":{"react":"latest","vite":"latest"}}',
                encoding="utf-8",
            )
            profile = scan_project(root)
            self.assertIn("react", profile["frameworks"])
            self.assertEqual(profile["commands"]["test"], "npm test")
            self.assertEqual(profile["commands"]["build"], "npm run build")

    def test_route_keeps_git_summary_local(self):
        route = route_task("show git status and suggest a commit message")
        self.assertEqual(route["route"], "L0")
        self.assertFalse(route["requires_confirmation"])

    def test_refactor_routes_to_cheap_coding_model_after_local_evidence(self):
        route = route_task("refactor dashboard components to simplify validation")
        self.assertEqual(route["route"], "L2")
        self.assertIn("refactor", route["agents"])

    def test_shipping_routes_to_cheap_model_and_deployment_agent(self):
        route = route_task("build and ship a small notes app")
        self.assertEqual(route["route"], "L2")
        self.assertIn("implementation", route["agents"])
        self.assertIn("deployment-ci", route["agents"])
        self.assertFalse(route["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
