"""The repository's policy decides acceptance, not the request's wording (#295 gate 1).

Gate 1 wants `completed` to rest on *an applicable passed policy*. The last way
around that was how the objective chose its acceptance requirements:

    if normalized_mode == "ship" or _TEST_REQUEST.search(objective_text):
        acceptance.append(AcceptanceRequirement.TESTS_PASS)

A regex over the request text. Asking OPai to **"fix the crash in parser.py"**
never says "test", so the objective required only an edit — and a diff alone was
enough to report *completed* on a change nobody had run. Phrasing decided
whether verification was required.

#590 resolves a real verification policy from the repository. That policy knows
whether the project has unit checks; the sentence the user typed does not.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.completion import (
    AcceptanceRequirement,
    objective_from_request,
    required_test_kinds,
)
from opaihub.verification_policy import resolve_verification_policy


class _Check:
    """A structural stand-in — completion reads policies by shape, not type."""

    def __init__(self, kind: str, requirement: str) -> None:
        self.kind = kind
        self.requirement = requirement


class _Policy:
    def __init__(self, *checks: _Check) -> None:
        self.checks = checks


class RequiredTestKindsTests(unittest.TestCase):
    def test_a_required_unit_check_demands_passing_tests(self) -> None:
        self.assertTrue(required_test_kinds(_Policy(_Check("unit", "required"))))

    def test_an_optional_check_does_not(self) -> None:
        # An optional check is a suggestion. Treating it as mandatory would
        # strand honest runs at "partial" forever.
        self.assertFalse(required_test_kinds(_Policy(_Check("unit", "optional"))))

    def test_a_required_non_test_check_does_not(self) -> None:
        # Lint being required does not mean *tests* passed; `tests_pass` is the
        # only acceptance requirement with executable proof behind it today.
        self.assertFalse(required_test_kinds(_Policy(_Check("lint", "required"))))

    def test_every_test_shaped_kind_counts(self) -> None:
        for kind in ("unit", "integration", "e2e"):
            with self.subTest(kind=kind):
                self.assertTrue(required_test_kinds(_Policy(_Check(kind, "required"))))

    def test_a_policy_without_checks_demands_nothing(self) -> None:
        self.assertFalse(required_test_kinds(_Policy()))

    def test_a_malformed_policy_is_not_treated_as_demanding(self) -> None:
        # Fail-open here is correct: inventing a requirement nothing can satisfy
        # would make every run partial. The verdict's own guards still apply.
        self.assertFalse(required_test_kinds(object()))
        self.assertFalse(required_test_kinds(None))


class ObjectiveAcceptanceTests(unittest.TestCase):
    TASK = "Fix the crash in parser.py"  # deliberately never says "test"

    def test_without_a_policy_the_old_heuristic_still_applies(self) -> None:
        objective = objective_from_request(self.TASK, mode="implement")
        self.assertNotIn(AcceptanceRequirement.TESTS_PASS, objective.acceptance)

    def test_a_policy_requiring_unit_checks_adds_tests_pass(self) -> None:
        # The regression this closes: phrasing no longer decides.
        objective = objective_from_request(
            self.TASK, mode="implement", policy=_Policy(_Check("unit", "required"))
        )
        self.assertIn(AcceptanceRequirement.TESTS_PASS, objective.acceptance)

    def test_a_policy_without_required_tests_does_not_add_it(self) -> None:
        # A repository with no tests must not be told to run tests forever.
        objective = objective_from_request(
            "Run the tests",
            mode="implement",
            policy=_Policy(_Check("lint", "required")),
        )
        self.assertNotIn(AcceptanceRequirement.TESTS_PASS, objective.acceptance)

    def test_the_policy_overrides_the_wording_in_both_directions(self) -> None:
        # Wording said tests; policy says they are not required -> not required.
        loose = objective_from_request(
            "run the tests please", mode="implement", policy=_Policy()
        )
        self.assertNotIn(AcceptanceRequirement.TESTS_PASS, loose.acceptance)
        # Wording said nothing; policy requires them -> required.
        strict = objective_from_request(
            self.TASK, mode="implement", policy=_Policy(_Check("unit", "required"))
        )
        self.assertIn(AcceptanceRequirement.TESTS_PASS, strict.acceptance)

    def test_an_answer_only_run_is_untouched_by_policy(self) -> None:
        objective = objective_from_request(
            "What does this do?",
            mode="explain",
            policy=_Policy(_Check("unit", "required")),
        )
        self.assertEqual(objective.acceptance, (AcceptanceRequirement.ANSWER_PRESENT,))

    def test_an_edit_always_still_requires_edit_evidence(self) -> None:
        objective = objective_from_request(
            self.TASK, mode="implement", policy=_Policy(_Check("unit", "required"))
        )
        self.assertIn(AcceptanceRequirement.EXPECTED_EDIT, objective.acceptance)


class RealPolicyTests(unittest.TestCase):
    """Against a genuinely resolved policy, not a stand-in."""

    def test_a_repository_with_tests_requires_them_for_an_unworded_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp),
                files={
                    "app.py": "x = 1\n",
                    "tests/test_app.py": "def test_app():\n    assert True\n",
                },
                commit=True,
            )
            policy = resolve_verification_policy(
                Path(root),
                task="Fix the crash in parser.py",
                mode="implement",
                delivery="local",
            )
            self.assertEqual(policy.status, "ready")
            objective = objective_from_request(
                "Fix the crash in parser.py", mode="implement", policy=policy
            )
        # Without the policy this task completed on a diff alone.
        self.assertIn(AcceptanceRequirement.TESTS_PASS, objective.acceptance)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
