import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE_JSON = ROOT / "package.json"


class WebCiContractTests(unittest.TestCase):
    def test_web_job_enforces_security_unit_and_browser_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("contents: read", workflow)
        self.assertIn("\n  web-test:\n", workflow)
        web_job = workflow.split("\n  web-test:\n", maxsplit=1)[1]

        required_contract = {
            "supported Node runtime": 'node-version: "22"',
            "explicit Playwright server runtime": 'python-version: "3.13"',
            "reproducible install": "npm ci",
            "high-severity audit": "npm audit --audit-level=high",
            "unit tests": "npm run test:unit",
            "design token lint": "npm run test:tokens",
            "Chromium dependencies": "playwright install --with-deps chromium",
            "browser E2E tests": "npm run test:e2e",
            "failure artifacts": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        }
        for behavior, marker in required_contract.items():
            with self.subTest(behavior=behavior):
                self.assertIn(marker, web_job)

    def test_javascript_tooling_remains_development_only(self):
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

        self.assertNotIn("dependencies", package)
        self.assertIn("vitest", package["devDependencies"])
        self.assertIn("@playwright/test", package["devDependencies"])
        self.assertTrue(package["devDependencies"]["vitest"].startswith("^4."))


if __name__ == "__main__":
    unittest.main()
