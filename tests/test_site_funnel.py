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

    def test_private_access_keeps_public_install_urls_off_site(self):
        self.assertNotIn("raw.githubusercontent.com/MarcoLadeira/OPai", self.html)
        self.assertNotIn("install.ps1", self.html)
        self.assertNotIn("install.sh", self.html)
        self.assertIn("Private install command appears after paid access", self.html)
        self.assertIn("opai quickstart", self.html)
        self.assertIn("opai benchmark run --suite max --mode both", self.html)
        self.assertIn(
            "opai benchmark gate --min-effectiveness-index 95",
            self.html,
        )
        self.assertIn("opai savings --markdown", self.html)

    def test_page_uses_only_privacy_safe_cloudflare_analytics(self):
        lowered = self.html.lower()
        self.assertIn("static.cloudflareinsights.com/beacon.min.js", lowered)
        self.assertIn("replace_with_cloudflare_web_analytics_token", lowered)
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
        for token in ["$12", "$99", "$19", "$29"]:
            self.assertIn(token, self.html)

    def test_launch_ctas_match_go_to_market_plan(self):
        for token in ["Get OPai Access", "Buy Founding Pro", "Apply for Team Pilot"]:
            self.assertIn(token, self.html)
        self.assertIn("Controlled Alpha", self.html)
        self.assertIn("Founding Pro", self.html)
        self.assertIn("Team Pilot", self.html)

    def test_paid_access_does_not_use_public_repo_intake(self):
        self.assertNotIn("issues/new", self.html)
        self.assertNotIn("founding-pro-interest.yml", self.html)
        self.assertNotIn("team-pilot.yml", self.html)
        self.assertNotIn("benchmark-proof.yml", self.html)
        for relative in [
            ".github/ISSUE_TEMPLATE/founding-pro-interest.yml",
            ".github/ISSUE_TEMPLATE/team-pilot.yml",
            ".github/ISSUE_TEMPLATE/benchmark-proof.yml",
        ]:
            self.assertFalse((REPO / relative).exists(), relative)
        for token in [
            "PRIVATE_FOUNDING_PRO_CHECKOUT_URL",
            "PRIVATE_TEAM_PILOT_APPLY_URL",
            "PRIVATE_BENCHMARK_PROOF_URL",
        ]:
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
