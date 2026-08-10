import json
import unittest
from pathlib import Path

from scripts.ci_local import WEB_STEPS


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE_JSON = ROOT / "package.json"


class WebCiContractTests(unittest.TestCase):
    def test_web_job_enforces_security_unit_and_browser_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("contents: read", workflow)
        self.assertIn("\n  web-test:\n", workflow)
        web_job = workflow.split("\n  web-test:\n", maxsplit=1)[1]
        self.assertIn("runs-on: windows-latest", web_job)
        self.assertIn("timeout-minutes: 55", web_job)
        self.assertIn("--component web", web_job)

        workflow_contract = {
            "supported Node runtime": 'node-version: "22"',
            "explicit Playwright server runtime": 'python-version: "3.13"',
            "failure artifacts": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        }
        for behavior, marker in workflow_contract.items():
            with self.subTest(behavior=behavior):
                self.assertIn(marker, web_job)

        steps = {step.name: step for step in WEB_STEPS}
        command_contract = {
            "npm-ci": ["npm", "ci"],
            "npm-audit": ["npm", "audit", "--audit-level=high"],
            "web-unit": ["npm", "run", "test:unit"],
            "web-design-tokens": ["npm", "run", "test:tokens"],
            "playwright-browser-install": [
                "npx",
                "playwright",
                "install",
                "chromium",
            ],
            "web-e2e": ["npm", "run", "test:e2e", "--", "--workers=2"],
        }
        self.assertEqual(set(steps), set(command_contract))
        for name, command in command_contract.items():
            with self.subTest(step=name):
                self.assertEqual(steps[name].argv, command)
        self.assertEqual(steps["web-e2e"].timeout_seconds, 45 * 60)

    def test_javascript_tooling_remains_development_only(self):
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

        self.assertNotIn("dependencies", package)
        self.assertIn("vitest", package["devDependencies"])
        self.assertIn("@playwright/test", package["devDependencies"])
        self.assertTrue(package["devDependencies"]["vitest"].startswith("^4."))


if __name__ == "__main__":
    unittest.main()
