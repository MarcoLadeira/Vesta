"""Skill catalogue validation.

`vestahub validate` gated five registries in CI; skills were the one it did not
cover, and `vestahub skills doctor` checked only that each file exists. A skill
is instruction text a model acts on, so two failures were unguarded: frontmatter
drifting out of sync with the registry (the skill still exists, still passes an
existence check, and silently never activates again), and a state-changing skill
shipping with no stated boundary.

Every test here states the failure it prevents rather than the field it reads.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestahub.skill_validation import (
    MAX_DESCRIPTION_CHARS,
    MUTATING_CATEGORIES,
    validate_skills,
)

GOOD_BODY = (
    "Read the failing logs, reproduce locally, and patch minimally. "
    "Never push or merge without explicit confirmation."
)


class _Hub:
    """A throwaway hub tree so tests never depend on the shipped catalogue."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "skills").mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = []

    def add(
        self,
        skill_id: str,
        *,
        category: str = "coding",
        frontmatter_name: str | None = None,
        description: str = "Use when the tests fail and need diagnosis.",
        body: str = GOOD_BODY,
        write_file: bool = True,
        register: bool = True,
        **overrides,
    ) -> None:
        if write_file:
            directory = self.root / "skills" / skill_id
            directory.mkdir(parents=True, exist_ok=True)
            name = skill_id if frontmatter_name is None else frontmatter_name
            header = "---\n"
            if name is not None:
                header += f"name: {name}\n"
            if description is not None:
                header += f"description: {description}\n"
            header += "---\n"
            (directory / "SKILL.md").write_text(
                f"{header}# {skill_id}\n{body}\n", encoding="utf-8"
            )
        if register:
            entry = {
                "id": skill_id,
                "name": skill_id.replace("-", " ").title(),
                "path": f"skills/{skill_id}/SKILL.md",
                "category": category,
                "description": description or "registry description",
                "cost_policy": "L0 only",
                "enabled_by_default": True,
            }
            entry.update(overrides)
            self.entries.append(entry)

    def write(self) -> Path:
        (self.root / "skills" / "registry.yaml").write_text(
            json.dumps({"schema_version": 1, "skills": self.entries}, indent=2),
            encoding="utf-8",
        )
        return self.root


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.hub = _Hub(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self) -> dict:
        return validate_skills(self.hub.write())

    def assertFlags(self, fragment: str) -> None:
        report = self.check()
        self.assertFalse(report["ok"], f"expected a failure mentioning {fragment!r}")
        self.assertTrue(
            any(fragment in issue for issue in report["issues"]),
            f"no issue mentioned {fragment!r}; got {report['issues']}",
        )


class HealthyCatalogueTests(_Base):
    def test_a_well_formed_catalogue_passes(self) -> None:
        self.hub.add("ci-fixer", category="ci")
        self.hub.add("code-review", category="review")
        report = self.check()
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["registry"], "skills")

    def test_an_empty_catalogue_is_not_an_error(self) -> None:
        # A hub with no skills yet is a legitimate state, not a broken one.
        report = self.check()
        self.assertTrue(report["ok"], report["issues"])


class ActivationTests(_Base):
    """Failures that leave a skill present but unselectable."""

    def test_frontmatter_name_drifting_from_the_registry_id_is_caught(self) -> None:
        # The silent-deactivation case: the file exists, `skills doctor` is
        # happy, and the host can never match it again.
        self.hub.add("ci-fixer", frontmatter_name="ci_fixer")
        self.assertFlags("will not activate")

    def test_a_skill_with_no_frontmatter_is_caught(self) -> None:
        directory = self.hub.root / "skills" / "orphan"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("# Orphan\nJust prose.\n", encoding="utf-8")
        self.hub.entries.append(
            {
                "id": "orphan",
                "name": "Orphan",
                "path": "skills/orphan/SKILL.md",
                "category": "coding",
                "description": "Use when something needs doing sometimes.",
                "cost_policy": "L0 only",
            }
        )
        self.assertFlags("no --- frontmatter")

    def test_a_missing_description_is_caught(self) -> None:
        self.hub.add("ci-fixer", description=None)
        self.assertFlags("missing 'description'")

    def test_a_description_too_short_to_route_on_is_caught(self) -> None:
        self.hub.add("ci-fixer", description="fix ci")
        self.assertFlags("too short to drive activation")

    def test_a_description_that_swallowed_the_body_is_caught(self) -> None:
        self.hub.add("ci-fixer", description="x" * (MAX_DESCRIPTION_CHARS + 1))
        self.assertFlags("put detail in the body")

    def test_a_stub_body_is_caught(self) -> None:
        self.hub.add("ci-fixer", body="TODO")
        self.assertFlags("not a stub")


class SafetyBoundaryTests(_Base):
    """State-changing skills must say where they stop."""

    def test_a_mutating_skill_without_a_boundary_is_caught(self) -> None:
        self.hub.add(
            "gitops-pr",
            category="gitops",
            body="Summarize status and diff, then commit and open the PR.",
        )
        self.assertFlags("must state its boundary")

    def test_every_mutating_category_is_enforced(self) -> None:
        for category in sorted(MUTATING_CATEGORIES):
            with self.subTest(category=category):
                hub = _Hub(Path(tempfile.mkdtemp(dir=self._tmp.name)))
                hub.add(
                    "risky-skill",
                    category=category,
                    body="Do the thing and apply the change to the repository.",
                )
                report = validate_skills(hub.write())
                self.assertFalse(report["ok"], category)

    def test_a_stated_limit_satisfies_the_rule(self) -> None:
        # Any recognisable form of a limit counts — authors keep their voice,
        # what is enforced is that a boundary exists.
        for body in (
            "Apply the migration. Never drop a table without confirmation.",
            "Apply the change, but do not force push.",
            "Write reversible migrations and include rollback instructions.",
            "Do a dry-run first, then apply.",
            "This skill is read-only and installs nothing.",
        ):
            with self.subTest(body=body):
                hub = _Hub(Path(tempfile.mkdtemp(dir=self._tmp.name)))
                hub.add("db-skill", category="database", body=body)
                report = validate_skills(hub.write())
                self.assertTrue(report["ok"], f"{body!r} -> {report['issues']}")

    def test_a_read_only_category_needs_no_boundary(self) -> None:
        # The rule must not become noise on skills that cannot change anything.
        self.hub.add(
            "code-review", category="review", body="Read the diff and report defects."
        )
        report = self.check()
        self.assertTrue(report["ok"], report["issues"])


class RegistryConsistencyTests(_Base):
    def test_a_directory_missing_from_the_registry_is_caught(self) -> None:
        # Shipped but undiscoverable: dead weight that still looks like a
        # feature to anyone browsing the tree.
        self.hub.add("ci-fixer", category="ci")
        self.hub.add("ghost", register=False)
        self.assertFlags("not in the skills registry")

    def test_a_registry_entry_with_no_file_is_caught(self) -> None:
        self.hub.add("vanished", write_file=False)
        self.assertFlags("does not resolve to a file")

    def test_a_duplicate_id_is_caught(self) -> None:
        self.hub.add("ci-fixer", category="ci")
        self.hub.add("ci-fixer", category="ci")
        self.assertFlags("duplicate registry id")

    def test_a_missing_required_field_is_caught(self) -> None:
        self.hub.add("ci-fixer", category="ci", cost_policy="")
        self.assertFlags("missing cost_policy")

    def test_a_non_kebab_case_id_is_caught(self) -> None:
        self.hub.add("CI_Fixer")
        self.assertFlags("kebab-case")

    def test_a_corrupt_registry_reports_instead_of_raising(self) -> None:
        (self.hub.root / "skills" / "registry.yaml").write_text(
            "{not json", encoding="utf-8"
        )
        report = validate_skills(self.hub.root)
        self.assertFalse(report["ok"])
        self.assertTrue(any("not valid JSON" in i for i in report["issues"]))

    def test_a_missing_registry_reports_instead_of_raising(self) -> None:
        report = validate_skills(Path(self._tmp.name) / "no-such-hub")
        self.assertFalse(report["ok"])
        self.assertTrue(report["issues"])


class ShippedCatalogueTests(unittest.TestCase):
    """The catalogue Vesta actually ships must pass its own gate."""

    def test_the_real_skill_catalogue_is_valid(self) -> None:
        hub = Path(__file__).resolve().parent.parent / "hub"
        if not (hub / "skills" / "registry.yaml").is_file():
            self.skipTest("hub/skills not present in this checkout")
        report = validate_skills(hub)
        self.assertTrue(report["ok"], report["issues"])
        self.assertGreater(report["count"], 0)

    def test_validate_all_now_covers_skills(self) -> None:
        from vestahub.validator import validate_all

        root = Path(__file__).resolve().parent.parent
        names = {r["registry"] for r in validate_all(root)["registries"]}
        self.assertIn("skills", names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
