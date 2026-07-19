"""Funnel + positioning guards (business strategy GTM)."""

import re
import unittest
from pathlib import Path

from opai.cli import build_parser

REPO = Path(__file__).resolve().parents[1]


class SiteFunnelTests(unittest.TestCase):
    def setUp(self):
        self.html = (REPO / "site" / "index.html").read_text(encoding="utf-8")

    def test_page_exists_with_positioning(self):
        self.assertIn("AI Coding Cost Firewall", self.html)
        for client in ["Claude", "Codex", "Copilot", "Cursor", "Cline"]:
            self.assertIn(client, self.html)

    def test_free_alpha_has_no_paid_or_private_access_gate(self):
        self.assertNotIn("raw.githubusercontent.com/MarcoLadeira/OPai", self.html)
        self.assertNotIn("install.ps1", self.html)
        self.assertNotIn("install.sh", self.html)
        self.assertNotIn('python -m pip install "opai[desktop-gui]"', self.html)
        self.assertIn("Free public alpha", self.html)
        self.assertIn("pip install opai", self.html)
        for token in [
            "PRIVATE_FOUNDING_PRO_CHECKOUT_URL",
            "PRIVATE_TEAM_PILOT_APPLY_URL",
            "PRIVATE_BENCHMARK_PROOF_URL",
            "data-private-link",
            "data-checkout-provider",
        ]:
            self.assertNotIn(token, self.html)
        self.assertIn("opai savings --markdown", self.html)

    def test_page_uses_no_third_party_trackers(self):
        # The current page ships zero analytics; privacy-safe means none at all.
        lowered = self.html.lower()
        for tracker in [
            "google-analytics",
            "googletagmanager",
            "gtag(",
            "plausible",
            "mixpanel",
            "cloudflareinsights",
            "beacon.min.js",
        ]:
            self.assertNotIn(tracker, lowered)

    def test_free_alpha_has_no_paid_pricing_cards(self):
        for token in ["$12", "$99", "$19", "$29", "Founding Pro", "Team Pilot"]:
            self.assertNotIn(token, self.html)
        self.assertIn("$0", self.html)
        self.assertIn("Free public alpha", self.html)

    def test_launch_ctas_match_free_alpha_contract(self):
        for token in ["Try the playground", "View on GitHub", "Join Discussions"]:
            self.assertIn(token, self.html)
        self.assertIn("Free public alpha", self.html)

    def test_footer_uses_the_current_release_identifier(self):
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^release = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertIn(match.group(1).replace("-", " "), self.html)

    def test_free_alpha_has_no_paid_checkout_or_private_intake(self):
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
        self.assertNotIn("data-checkout", self.html.lower())
        self.assertNotIn("buy founding", self.html.lower())
        self.assertNotIn("paid access", self.html.lower())


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

    def test_readme_declares_a_fully_free_alpha(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("fully free", readme.lower())
        self.assertNotIn("Paid users and Team Pilot customers", readme)

    def test_current_onboarding_does_not_claim_an_archived_release_path(self):
        for relative in ["README.md", "docs/QUICKSTART.md"]:
            text = (REPO / relative).read_text(encoding="utf-8")
            self.assertIn("github.com/MarcoLadeira/OPai/releases", text, relative)
            self.assertIn("no public", text.lower(), relative)
            self.assertNotIn(
                "current verified release path is described in the release notes",
                text.lower(),
                relative,
            )

    def test_public_copy_does_not_advertise_an_unpublished_install_path(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        quickstart = (REPO / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
        launch_checklist = (REPO / "docs" / "LAUNCH_CHECKLIST.md").read_text(
            encoding="utf-8"
        )
        publishing = (REPO / "docs" / "PUBLISHING.md").read_text(encoding="utf-8")
        site_readme = (REPO / "site" / "README.md").read_text(encoding="utf-8")
        site = (REPO / "site" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("# From PyPI / a published wheel:", readme)
        self.assertIn("no public desktop artifact", readme.lower())
        self.assertIn("python -m pip install -e .", quickstart)
        self.assertIn("after a verified artifact is published", launch_checklist)
        self.assertIn(
            "No public package, desktop artifact, or GitHub installation command is available",
            publishing,
        )
        self.assertNotIn("pipx install git+", publishing)
        self.assertNotIn("free alpha install", site_readme.lower())
        self.assertNotIn('aria-label="Windows install command"', site)
        self.assertNotIn('aria-label="macOS and Linux install command"', site)

    def test_paid_launch_strategy_documents_are_explicitly_archived(self):
        for relative in [
            "docs/BUSINESS_STRATEGY.md",
            "docs/COMMERCIAL_ACCESS_AND_IP_PROTECTION.md",
        ]:
            text = (REPO / relative).read_text(encoding="utf-8")
            normalized = " ".join(text.lower().split())
            self.assertIn("Archived Pre-Free-Launch", text, relative)
            self.assertIn("must not be used", normalized, relative)


if __name__ == "__main__":
    unittest.main()
