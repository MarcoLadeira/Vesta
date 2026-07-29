"""Documentation contract for issue #554 launch operations."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "docs" / "LAUNCH_OPERATIONS.md"
CHECKLIST = ROOT / "docs" / "LAUNCH_CHECKLIST.md"
README = ROOT / "README.md"


def section(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index + len(start))
    return text[start_index:end_index]


class LaunchOperationsDocumentationTests(unittest.TestCase):
    def test_scorecard_and_waiver_fields_exist(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        scorecard = section(text, "## Launch readiness scorecard", "## Waiver record")
        for required in [
            "No launch decision may be marked ready while an unwaived P0 gate is open.",
            "#518",
            "#293",
            "#515",
            "#529",
            "#526–#528",
            "#557",
            "#558",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, scorecard)
        waiver = section(
            text, "## Waiver record", "## Public claims and launch-surface consistency"
        )
        for required in [
            "owner",
            "evidence",
            "impact",
            "mitigation",
            "expiry",
            "rollback plan",
        ]:
            with self.subTest(waiver_field=required):
                self.assertIn(required, waiver)

    def test_scorecard_defines_minimum_evidence_thresholds(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        scorecard = section(text, "## Launch readiness scorecard", "## Waiver record")
        for required in [
            "### Minimum evidence thresholds",
            "successful install",
            "first verified outcome",
            "receipt completeness",
            "support readiness",
            "Day-7 retention",
            "at least 10",
            ">= 90%",
            "inconclusive",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, scorecard)

    def test_channel_playbooks_require_adaptation_and_disclosure(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for channel in [
            "### Hacker News",
            "### Reddit",
            "### GitHub",
            "### LinkedIn/X",
            "### Direct founder outreach",
        ]:
            with self.subTest(channel=channel):
                self.assertIn(channel, text)
        self.assertIn("maker relationship", text)
        self.assertIn("Do not copy-paste promotion", text)

    def test_incidents_claims_and_privacy_are_operational(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        claims = section(
            text, "## Public claims and launch-surface consistency", "## Kill switch"
        )
        for required in [
            "reproducible evidence",
            "hypothesis or early observation",
            "source dataset",
            "method",
            "version",
            "known limitations",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, claims)
        incidents = section(
            text,
            "## Support, security, and incident response",
            "## Privacy-safe attribution and cohort review",
        )
        for required in [
            "Security report",
            "Secret exposure",
            "Repository mutation incident",
            "Billing or cost discrepancy",
            "Provider outage",
            "Rehearsal",
        ]:
            with self.subTest(incident=required):
                self.assertIn(required, incidents)
        privacy = section(
            text,
            "## Privacy-safe attribution and cohort review",
            "## Post-launch reviews",
        )
        self.assertIn("consent-compatible", privacy)
        self.assertIn("separate from product telemetry", privacy)

    def test_demo_and_content_assets_have_evidence_requirements(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        assets = section(text, "## Demo and content asset checklist", "## Kill switch")
        for required in [
            "Public demo",
            "Technical explainer",
            "Evidence-backed case study",
            "owner",
            "status",
            "evidence link",
            "known limitations",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, assets)

    def test_reviews_require_outcomes_owners_and_backlog_updates(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for required in [
            "24-hour",
            "7-day",
            "30-day",
            "qualified install",
            "first verified outcome",
            "Day-7",
            "four-week",
            "useful feedback",
            "support burden",
            "continue",
            "change",
            "stop",
            "canonical backlog update",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_feedback_routes_to_a_canonical_prioritised_issue(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        support = section(
            text,
            "## Support, security, and incident response",
            "## Privacy-safe attribution and cohort review",
        )
        self.assertIn("existing canonical issue or a genuinely new issue", support)
        self.assertIn("reproduction, owner, and priority", support)

    def test_checklist_requires_adaptation_not_verbatim_promotion(self):
        checklist = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn("Canonical positioning statement", checklist)
        self.assertNotIn("use verbatim", checklist)

    def test_navigation_points_to_the_canonical_runbook(self):
        for path in [CHECKLIST, README]:
            with self.subTest(path=path.name):
                self.assertIn("LAUNCH_OPERATIONS.md", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
