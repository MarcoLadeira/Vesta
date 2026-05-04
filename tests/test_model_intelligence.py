import json
import subprocess
import sys
import unittest
from pathlib import Path

from opaihub.model_intelligence import recommend_model
from opaihub.loader import registry_items
from opaihub.skills import skill_items, skill_registry


class ModelIntelligenceTests(unittest.TestCase):
    def test_skill_registry_contains_more_than_25_skills_with_files(self):
        root = Path.cwd()
        registry = skill_registry(root)
        skills = skill_items(root)

        self.assertEqual(registry["schema_version"], 1)
        self.assertGreaterEqual(len(skills), 25)
        for skill in skills:
            self.assertIn("id", skill)
            self.assertIn("path", skill)
            self.assertTrue((root / "hub" / skill["path"]).exists(), skill["id"])

    def test_recommend_model_routes_simple_status_to_local_or_l0(self):
        result = recommend_model(Path.cwd(), "summarize git status and changed files")

        self.assertEqual(result["task_type"], "gitops_summary")
        self.assertIn(result["recommended_model_tier"], {"L0", "L1"})
        self.assertFalse(result["requires_confirmation"])
        self.assertEqual(
            result["policy"], "local evidence first; cheapest capable model"
        )

    def test_recommend_model_routes_security_release_to_stronger_confirmed_tier(self):
        result = recommend_model(
            Path.cwd(), "prepare a production security release and rollback plan"
        )

        self.assertEqual(result["task_type"], "release_security")
        self.assertIn(result["recommended_model_tier"], {"L0", "L1", "L2"})
        self.assertNotEqual(result["recommended_model_tier"], "L3")
        self.assertTrue(result["requires_confirmation"])
        self.assertIn("security", result["matched_signals"])

    def test_opai_models_recommend_cli_outputs_json(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "opai",
                "models",
                "recommend",
                "fix failing tests cheaply",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["task_type"], "test_failure_debug")
        self.assertIn("recommended_model_id", payload)

    def test_cost_saving_researched_tools_are_registered_disabled_by_default(self):
        tools = {tool["id"]: tool for tool in registry_items("tools", Path.cwd())}
        expected = {
            "routellm",
            "litellm",
            "promptfoo",
            "repomix",
            "ast-grep",
            "semgrep",
            "aider",
            "continue-dev",
            "ollama",
            "lm-studio",
            "context7-mcp",
        }

        self.assertTrue(expected.issubset(tools), expected - set(tools))
        for tool_id in expected:
            self.assertFalse(tools[tool_id]["enabled_by_default"], tool_id)
            self.assertIn(tools[tool_id]["cost_level"], {"free", "free-network", "low"})


if __name__ == "__main__":
    unittest.main()
