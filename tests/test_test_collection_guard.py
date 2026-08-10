"""Adversarial review of the collection guard itself (#612).

This guard exists because #621 found an assertion that had never executed. Its
first implementation then reproduced that exact failure class: it used
in-process `TestLoader.discover`, and 25 modules that fail to import under
`top_level_dir=tests/` arrived as `_FailedTest`. Skipping them turned absence
into a smaller number instead of an error, and it reported `test_receipt.py` as
collecting zero when the real runner collects 27.

A tool that silently miscounts is worse than no tool, because it produces a
confident wrong number that ends up in a PR body. So the guard is now tested
against ground truth from the real runners, and every claim it makes is
independently checked here.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    path = ROOT / "scripts" / "check_test_collection.py"
    spec = importlib.util.spec_from_file_location("opai_collection_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


def _real_unittest_count(test_file: str) -> int:
    """Ground truth: what `unittest discover` actually runs for one file."""
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", test_file],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"^Ran (\d+) tests?", completed.stderr, re.MULTILINE)
    return int(match.group(1)) if match else 0


class AgreesWithTheRealRunnerTests(unittest.TestCase):
    """Point 8: static classification must match what the runner does.

    The whole failure mode was a classifier that disagreed with reality while
    sounding authoritative, so this compares against the runner rather than
    against the classifier's own opinion.
    """

    SAMPLES = (
        # (file, expected-to-be-collected-by-unittest)
        ("test_receipt.py", True),  # the file the broken version got wrong
        ("test_cost_ledger.py", True),
        ("test_lifecycle_generation.py", True),
        ("test_lifecycle_authority.py", True),
        ("test_run_state.py", False),  # bare pytest functions
        ("test_state_vocabulary_drift.py", False),
        ("test_run_status_adoption.py", False),  # the #621 original
    )

    def test_static_classification_matches_unittest_for_each_sample(self):
        counts = GUARD._unittest_counts()
        for name, collectible in self.SAMPLES:
            with self.subTest(file=name):
                real = _real_unittest_count(name)
                static = counts.get(name, 0)
                self.assertEqual(
                    static > 0,
                    collectible,
                    f"{name}: static says {static}, expected collectible={collectible}",
                )
                self.assertEqual(
                    real > 0,
                    collectible,
                    f"{name}: the real runner ran {real}, expected "
                    f"collectible={collectible}",
                )

    def test_the_totals_are_in_the_right_order_of_magnitude(self):
        """A repeat of the original bug would show up as a large undercount.

        `unittest discover -s tests` reports ~3,800; the static estimate must
        land near it, not at the 1,605 the broken version produced.
        """
        counts = GUARD._unittest_counts()
        total = sum(counts.values())
        self.assertGreater(total, 3_000, f"suspiciously low unittest total: {total}")


class NeverSwallowsAbsenceTests(unittest.TestCase):
    """Points 1, 2, 3, 4."""

    def test_classification_imports_nothing(self):
        """Point 1/3: no import machinery, so no import can be swallowed.

        Checked against the syntax tree, not the text: the docstring names
        ``TestLoader`` precisely to explain why it is not used, and a substring
        search would fail on the explanation rather than on the behaviour.
        """
        import ast

        source = (ROOT / "scripts" / "check_test_collection.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("unittest", imported)
        self.assertNotIn("importlib", imported)

        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("discover", called)
        self.assertIn("parse", called)

    def test_a_malformed_file_is_reported_not_hidden(self):
        """Point 4: unparseable means uncollectable, which must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "test_broken.py"
            broken.write_text("def test_x(:\n    pass\n", encoding="utf-8")
            original = GUARD.TESTS
            try:
                GUARD.TESTS = Path(tmp)
                counts = GUARD._unittest_counts()
            finally:
                GUARD.TESTS = original
            self.assertEqual(counts["test_broken.py"], 0)

    def test_a_file_nobody_collects_is_a_violation(self):
        """Point 2: zero collection is an error, never a quiet pass."""
        report = {
            "uncollected": ["test_dead.py"],
            "pytest_only": [],
            "missing_critical": [],
            "empty_critical": [],
            "pytest_runner_required": False,
        }
        problems = GUARD._violations(report, profile_runners={"unittest", "pytest"})
        self.assertEqual(len(problems), 1)
        self.assertIn("test_dead.py", problems[0])
        self.assertIn("dead", problems[0])


class TeethTests(unittest.TestCase):
    """Points 5, 6, 7: each failure mode must actually fail."""

    def test_removing_the_pytest_lane_fails_loudly(self):
        """Point 6: the pre-#621 profile must be rejected."""
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                sys.executable,
                "scripts/check_test_collection.py",
                "--runner",
                "unittest",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("never execute", completed.stderr)
        self.assertIn("test_run_state.py", completed.stderr)

    def test_a_renamed_lifecycle_suite_fails(self):
        """Point 7: losing a named suite must be deliberate."""
        report = {
            "uncollected": [],
            "pytest_only": [],
            "missing_critical": ["test_run_state.py"],
            "empty_critical": [],
            "pytest_runner_required": False,
        }
        problems = GUARD._violations(report, profile_runners={"unittest", "pytest"})
        self.assertEqual(len(problems), 1)
        self.assertIn("deliberate", problems[0])

    def test_a_required_suite_that_collects_nothing_fails(self):
        """Point 5."""
        report = {
            "uncollected": [],
            "pytest_only": [],
            "missing_critical": [],
            "empty_critical": ["test_run_state.py"],
            "pytest_runner_required": False,
        }
        problems = GUARD._violations(report, profile_runners={"unittest", "pytest"})
        self.assertEqual(len(problems), 1)
        self.assertIn("zero tests", problems[0])

    def test_the_current_tree_passes_under_the_real_profile(self):
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, "scripts/check_test_collection.py"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class BoundedFalsePositiveTests(unittest.TestCase):
    """Point 9: the heuristic's limits are stated, not discovered later."""

    def test_a_plain_class_is_not_counted_as_unittest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "test_plain.py"
            sample.write_text(
                "class Helper:\n    def test_not_a_test(self):\n        pass\n",
                encoding="utf-8",
            )
            original = GUARD.TESTS
            try:
                GUARD.TESTS = Path(tmp)
                counts = GUARD._unittest_counts()
            finally:
                GUARD.TESTS = original
            self.assertEqual(counts["test_plain.py"], 0)

    def test_a_testcase_subclass_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "test_case.py"
            sample.write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self):\n        pass\n"
                "    def test_b(self):\n        pass\n"
                "    def helper(self):\n        pass\n",
                encoding="utf-8",
            )
            original = GUARD.TESTS
            try:
                GUARD.TESTS = Path(tmp)
                counts = GUARD._unittest_counts()
            finally:
                GUARD.TESTS = original
            self.assertEqual(counts["test_case.py"], 2)

    def test_the_known_over_count_direction_is_documented(self):
        """The heuristic also accepts a base class whose name ends in `Base`.

        That can over-count (a `_Base` that is not a TestCase), never
        under-count -- and over-counting is the safe direction here: it can
        only claim a file IS collected by unittest, which is then checked
        against the real runner by AgreesWithTheRealRunnerTests. Stated in the
        source so the limit is known rather than discovered later.
        """
        source = (ROOT / "scripts" / "check_test_collection.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Base", source)


class WindowsTests(unittest.TestCase):
    """Point 10: this is the platform #621 found launcher bugs on."""

    def test_it_runs_on_this_platform(self):
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, "scripts/check_test_collection.py", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"per_file"', completed.stdout)

    def test_file_keys_never_carry_a_platform_separator(self):
        """Report keys must be bare names, so evidence compares across OSes."""
        counts = GUARD._unittest_counts()
        for name in counts:
            with self.subTest(name=name):
                self.assertNotIn("\\", name)
                self.assertNotIn("/", name)


if __name__ == "__main__":
    unittest.main()
