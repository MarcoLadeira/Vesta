# Issue #554 Launch Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Give Vesta a test-enforced, evidence-gated launch-operations runbook that closes #554 without declaring the parent launch epic ready.

**Architecture:** docs/LAUNCH_OPERATIONS.md is the canonical operational source. docs/LAUNCH_CHECKLIST.md stays the concise entry point, README makes it discoverable, and a focused Python unittest locks the required safety rules and cross-document links.

**Tech Stack:** Markdown documentation and Python standard-library unittest.

---

### Task 1: Write the failing documentation contract

**Files:**
- Create: tests/test_launch_operations.py
- Test: tests/test_launch_operations.py

- [ ] **Step 1: Add the following test file**

~~~python
"""Documentation contract for issue #554 launch operations."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "docs" / "LAUNCH_OPERATIONS.md"
CHECKLIST = ROOT / "docs" / "LAUNCH_CHECKLIST.md"
README = ROOT / "README.md"


class LaunchOperationsDocumentationTests(unittest.TestCase):
    def test_scorecard_and_waiver_fields_exist(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for required in [
            "# Launch Operations Runbook",
            "## Launch readiness scorecard",
            "No launch decision may be marked ready while an unwaived P0 gate is open.",
            "#518", "#293", "#515", "#529", "#526–#528", "#557", "#558",
            "owner", "evidence", "impact", "mitigation", "expiry", "rollback plan",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_channel_playbooks_require_adaptation_and_disclosure(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for channel in [
            "### Hacker News", "### Reddit", "### GitHub",
            "### LinkedIn/X", "### Direct founder outreach",
        ]:
            with self.subTest(channel=channel):
                self.assertIn(channel, text)
        self.assertIn("maker relationship", text)
        self.assertIn("Do not copy-paste promotion", text)

    def test_incidents_claims_and_privacy_are_operational(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for required in [
            "reproducible evidence", "hypothesis or early observation",
            "source dataset", "method", "version", "known limitations",
            "consent-compatible", "separate from product telemetry",
            "Security report", "Secret exposure", "Repository mutation incident",
            "Billing or cost discrepancy", "Provider outage",
            "downloads, package publication, update channel, website claims, and incident messaging",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_reviews_require_outcomes_owners_and_backlog_updates(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        for required in [
            "24-hour", "7-day", "30-day", "qualified install",
            "first verified outcome", "Day-7", "four-week", "useful feedback",
            "support burden", "continue", "change", "stop", "canonical backlog update",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_navigation_points_to_the_canonical_runbook(self):
        for path in [CHECKLIST, README]:
            with self.subTest(path=path.name):
                self.assertIn("LAUNCH_OPERATIONS.md", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Step 2: Verify the red state**

Run: python -m unittest discover -s tests -p "test_launch_operations.py" -v

Expected: all runbook-dependent tests fail because docs/LAUNCH_OPERATIONS.md does not exist; this proves the contract detects the missing deliverable.

### Task 2: Implement the canonical runbook and navigation

**Files:**
- Create: docs/LAUNCH_OPERATIONS.md
- Modify: docs/LAUNCH_CHECKLIST.md
- Modify: README.md
- Test: tests/test_launch_operations.py

- [ ] **Step 1: Add docs/LAUNCH_OPERATIONS.md**

Start the document with these exact headings:

~~~markdown
# Launch Operations Runbook

## Launch readiness scorecard
## Waiver record
## Public claims and launch-surface consistency
## Kill switch and rollback
## Channel playbooks
### Hacker News
### Reddit
### GitHub
### LinkedIn/X
### Direct founder outreach
## Support, security, and incident response
## Privacy-safe attribution and cohort review
## Post-launch reviews
~~~

The scorecard names #518, #293, #515, #529, #526–#528, #557, and #558; prohibits readiness while an unwaived P0 is open; treats low samples as inconclusive; and sends any waiver to #518 with owner, evidence, impact, mitigation, expiry, and rollback plan. It pre-registers every benchmark/savings claim with source dataset, method, version, and known limitations. It requires reproducible evidence or a hypothesis or early observation label for every public claim, and verifies install, privacy, support, known limitations, and rollback information across launch surfaces.

Each channel playbook requires current rule review, maker relationship disclosure, adapted language, evidence-backed links, and an answer path. Include the exact shared rule: Do not copy-paste promotion.

The incident section separates ordinary support from security reports, and gives rehearsal paths for Security report, Secret exposure, Repository mutation incident, Billing or cost discrepancy, and Provider outage. The kill switch covers downloads, package publication, update channel, website claims, and incident messaging.

Attribution is consent-compatible and separate from product telemetry. It evaluates qualified install, first verified outcome, Day-7 and four-week retained verified use, useful feedback, and support burden; impressions, votes, downloads, and raw sign-ups remain diagnostic signals only. The 24-hour, 7-day, and 30-day reviews record evidence, owner, continue/change/stop decision, and canonical backlog update.

- [ ] **Step 2: Update concise navigation**

At the top of docs/LAUNCH_CHECKLIST.md, link to LAUNCH_OPERATIONS.md and say that the checklist supplies go/no-go evidence, not permission to launch. Keep the existing positioning and the caveat that no public artifact is currently available. In README’s linked launch material, add a link labelled launch operations runbook targeting docs/LAUNCH_OPERATIONS.md immediately before the archived go-to-market links.

- [ ] **Step 3: Verify the green state**

Run: python -m unittest discover -s tests -p "test_launch_operations.py" -v

Expected: 5 tests pass.

- [ ] **Step 4: Commit the green implementation**

Run: git add docs/LAUNCH_OPERATIONS.md docs/LAUNCH_CHECKLIST.md README.md tests/test_launch_operations.py && git commit -m "docs: add evidence-gated launch operations"

Expected: the commit includes only the canonical #554 runbook, its navigation, and the contract test.

### Task 3: Validate and publish

**Files:**
- Verify: docs/LAUNCH_OPERATIONS.md
- Verify: docs/LAUNCH_CHECKLIST.md
- Verify: README.md
- Verify: tests/test_launch_operations.py

- [ ] **Step 1: Run focused regressions**

Run:

~~~powershell
python -m unittest discover -s tests -p "test_launch_operations.py" -v
python -m unittest discover -s tests -p "test_no_paid_era_language.py" -v
python -m unittest discover -s tests -p "test_positioning_and_cli.py" -v
~~~

Expected: all focused tests pass, including free-public-alpha and existing positioning contracts.

- [ ] **Step 2: Run repository validation**

Run:

~~~powershell
python -m ruff check .
python -m ruff format --check .
python -m vestahub validate
git diff origin/main...HEAD --check
~~~

Expected: every command exits 0 and the whitespace check is clean.

- [ ] **Step 3: Run the broader Python suite and review scope**

Run:

~~~powershell
python -m unittest discover -s tests
git diff origin/main...HEAD -- README.md docs/LAUNCH_CHECKLIST.md docs/LAUNCH_OPERATIONS.md tests/test_launch_operations.py
~~~

Expected: all tests pass and the diff adds no launch-ready claim, secret-like content, or unrelated behaviour.

- [ ] **Step 4: Create and merge the issue-resolution PR**

Push branch codex/issue-554-launch-operations. Create a ready-for-review PR titled docs: add evidence-gated launch operations; its body must contain Closes #554 and Part of #530. Never use Closes #530, because #519 and #362 remain open canonical work. Merge after every PR check is green and confirm #554 closed.
