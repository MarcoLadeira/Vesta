"""Funnel + positioning guards (business strategy GTM)."""

import unittest
from pathlib import Path

from opai.cli import build_parser

REPO = Path(__file__).resolve().parents[1]


class SiteFunnelTests(unittest.TestCase):
    def setUp(self):
        self.html = (REPO / "site" / "index.html").read_text(encoding="utf-8")

    def test_page_exists_with_positioning(self):
        self.assertIn("AI coding cost firewall", self.html)
        for client in ["Claude", "Codex", "Copilot", "Cursor", "Cline"]:
            self.assertIn(client, self.html)

    def test_install_commands_present(self):
        self.assertIn("install.ps1", self.html)
        self.assertIn("install.sh", self.html)
        self.assertIn("opai quickstart", self.html)

    def test_page_is_telemetry_free(self):
        lowered = self.html.lower()
        self.assertNotIn("<script", lowered)
        for tracker in [
            "google-analytics",
            "googletagmanager",
            "gtag(",
            "plausible",
            "mixpanel",
        ]:
            self.assertNotIn(tracker, lowered)

    def test_pricing_matches_editions(self):
        # $12/mo, $99/yr, $19/user — must match hub/editions.yaml.
        for token in ["$12", "$99", "$19"]:
            self.assertIn(token, self.html)


class StrategyAndCommandsTests(unittest.TestCase):
    def test_business_strategy_is_in_repo(self):
        text = (REPO / "docs" / "BUSINESS_STRATEGY.md").read_text(encoding="utf-8")
        self.assertIn("AI coding cost firewall", text)

    def test_new_commands_registered(self):
        parser = build_parser()
        commands = set()
        for action in parser._subparsers._group_actions:
            if hasattr(action, "choices") and action.choices:
                commands = set(action.choices)
                break
        for name in ["quickstart", "why", "share", "metrics", "context", "test"]:
            self.assertIn(name, commands, name)

    def test_readme_links_funnel_and_strategy(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("site/index.html", readme)
        self.assertIn("docs/BUSINESS_STRATEGY.md", readme)


if __name__ == "__main__":
    unittest.main()
