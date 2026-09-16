"""Free-alpha language contract (#358).

Vesta launched as a **free public alpha**. The pre-free-launch paid strategy
(Founding Pro / Team Pilot pricing tiers, private/paid install, controlled alpha)
survives only in clearly *archived* strategy docs. These guards keep the audit
from silently regressing:

* user-facing entry points (README, CLI, editions text, marketing site) never
  advertise a paid tier or pricing, so a new user reading the repo top to bottom
  only ever sees the free-alpha message;
* the archived business-strategy docs stay clearly marked archived, so their
  historical paid language can never be mistaken for current policy.

This is the enforcement the launch-audit plan called for and pairs with the
existing site-funnel guard in ``tests/test_site_funnel.py``.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The surfaces a new user actually reads first. None may advertise a paid tier.
USER_FACING = [
    ROOT / "README.md",
    ROOT / "vestahub" / "editions.py",
    ROOT / "vesta" / "cli.py",
    ROOT / "vestahub" / "cli.py",
    ROOT / "site" / "index.html",
]

# Pre-free-launch paid-era pricing/tier language — never current policy.
BANNED = [
    r"Founding Pro",
    r"Team Pilot",
    r"\$12\s*/\s*month",
    r"\$99\s*/\s*year",
    r"\$199",
    r"private install",
    r"paid access",
    r"paid users",
    r"controlled alpha",
]
_BANNED_RE = re.compile("|".join(BANNED), re.IGNORECASE)

# Dated / strategy docs that describe the archived paid model. They may KEEP the
# paid language as history, but must stay clearly marked archived.
PAID_ERA_DOCS = [
    "BUSINESS_STRATEGY.md",
    "COMMERCIAL_ACCESS_AND_IP_PROTECTION.md",
    "GO_TO_MARKET_30_DAY_PLAN.md",
    "LAUNCH_REVENUE_RUNBOOK.md",
    "RELEASE_0_2_0_ALPHA_1.md",
    "EFFECTIVENESS_AND_SECURITY_AUDIT_2026_06_21.md",
]
_ARCHIVE_MARKER = re.compile(
    r"archived|historical record|historical release|superseded|not current policy",
    re.IGNORECASE,
)


class UserFacingHasNoPaidEraLanguageTests(unittest.TestCase):
    def test_entry_points_are_free_alpha_clean(self):
        for path in USER_FACING:
            with self.subTest(file=path.name):
                self.assertTrue(path.exists(), f"missing user-facing file: {path}")
                text = path.read_text(encoding="utf-8", errors="replace")
                hits = sorted({m.group(0) for m in _BANNED_RE.finditer(text)})
                self.assertEqual(
                    hits,
                    [],
                    f"{path.name} contains pre-free-launch paid-era language: {hits}",
                )


class PaidEraDocsStayArchivedTests(unittest.TestCase):
    def test_each_paid_era_doc_is_marked_archived(self):
        for name in PAID_ERA_DOCS:
            path = ROOT / "docs" / name
            with self.subTest(doc=name):
                self.assertTrue(path.exists(), f"missing paid-era doc: {path}")
                head = "\n".join(
                    path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]
                )
                self.assertRegex(
                    head,
                    _ARCHIVE_MARKER,
                    f"{name} must carry an archived/historical marker in its header "
                    "so its paid-era content is not mistaken for current policy",
                )


if __name__ == "__main__":
    unittest.main()
