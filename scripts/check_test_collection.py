"""Fail when a test file is not collected by any runner the profile runs (#612).

#621 found `tests/test_run_status_adoption.py` asserting a real lifecycle
invariant that had **never executed**: it is a bare pytest function, and the
only full-suite runner in the gate was `unittest discover`, which collects
nothing from such a file. The assertion existed, passed review, and was worth
exactly nothing.

Re-measuring for #612 found that was not one file but a class of failure. 20 of
238 test files collect **0** tests under `unittest discover`, and what they
contain is not random -- it is concentrated almost entirely in the consistency
contracts this epic owns:

    test_completion_contract.py     101 tests   completion truth (#522)
    test_verification_policy.py      21 tests   verification (#522)
    test_verification_execution.py   21 tests   verification (#522)
    test_run_journal.py              31 tests   durable events (#517)
    test_cancellation_lifecycle.py   17 tests   cancellation (#614)
    test_run_state.py                15 tests   the canonical state machine
    test_state_vocabulary_drift.py   11 tests   the anti-drift tripwire
    test_run_state_parity.py          8 tests   cross-surface parity
    test_runtime_phase_parity.py      8 tests   phase parity
    test_run_status_adoption.py       6 tests   (the one #621 found)
    ... 10 more, 333 tests in total

They run today only because #621 happened to add a `hostile-pytest` step -- an
*incidental* rescue, in the hostile-environment lane, not a guarantee. Delete
or reorder that one step and the tests guarding lifecycle, verification,
cancellation and durable events silently vanish again, with every suite still
green.

So a test file existing is not qualification, and a passing suite is not proof
that the assertions inside it ran. This check makes the coupling explicit and
enforced:

* every ``tests/test_*.py`` must be collected by at least one runner the
  profile actually executes -- so removing the pytest lane *fails here* rather
  than silently dropping 40 lifecycle tests;
* a file collected by nobody is dead weight and fails;
* the lifecycle-critical suites are named explicitly, so deleting or renaming
  one is a deliberate, reviewed act rather than an accident.

Collection only -- nothing is executed, so this is seconds, not minutes, and is
safe to run in the fast lane on every pull request.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess  # nosec B404 - fixed argv, no shell
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

#: Suites that carry the #612 lifecycle contract. Losing collection on any of
#: these is the specific accident #621 already suffered once, so they are named
#: rather than merely swept up by the directory scan.
LIFECYCLE_CRITICAL = (
    "test_run_state.py",
    "test_run_state_parity.py",
    "test_run_status_adoption.py",
    "test_state_vocabulary_drift.py",
    "test_lifecycle_generation.py",
    "test_lifecycle_properties.py",
    "test_illegal_transitions.py",
    "test_background_status_totality.py",
    "test_legacy_status_boundaries.py",
    "test_cancellation_lifecycle.py",
    "test_awaiting_input_state.py",
    "test_lifecycle_authority.py",
)


def _unittest_counts() -> dict[str, int]:
    """Tests per file that ``unittest discover`` can collect, read statically.

    Deliberately AST, not ``TestLoader.discover``. The first version of this
    check used in-process discovery and undercounted badly -- 25 of 237 modules
    fail to import when ``top_level_dir`` is ``tests/``, and they arrive as
    ``_FailedTest`` objects. Skipping those (to avoid attributing a broken
    import to a real file) silently discarded them, which is the very failure
    mode this script exists to prevent: absence quietly becoming a number
    rather than an error. It reported ``test_receipt.py`` as collecting zero
    when ``unittest discover -p test_receipt.py`` runs 27.

    Static classification has neither problem. ``unittest`` collects test
    methods on ``TestCase`` subclasses and nothing else, which is decidable
    from the syntax tree, needs no imports, cannot be skewed by import order,
    and runs in milliseconds.
    """

    counts: dict[str, int] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        except SyntaxError:
            counts[path.name] = 0
            continue
        total = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # `unittest.TestCase`, `TestCase`, or a local base whose name ends
            # in one of those -- the repo uses `_Base(unittest.TestCase)`
            # subclasses, whose methods unittest still collects.
            bases = {
                base.attr
                if isinstance(base, ast.Attribute)
                else getattr(base, "id", "")
                for base in node.bases
            }
            if not any(
                name.endswith("TestCase") or name.endswith("Base") for name in bases
            ):
                continue
            total += sum(
                1
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name.startswith("test")
            )
        counts[path.name] = total
    return counts


def _pytest_counts() -> dict[str, int]:
    """Tests per file that pytest collects, in one pass."""

    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    counts: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        line = line.strip()
        if "::" not in line or not line.startswith("tests"):
            continue
        name = Path(line.split("::", 1)[0]).name
        counts[name] = counts.get(name, 0) + 1
    return counts


def collect_report() -> dict[str, object]:
    """Per-file collection counts plus every violation found."""

    files = sorted(p.name for p in TESTS.glob("test_*.py"))
    unittest_counts = _unittest_counts()
    pytest_counts = _pytest_counts()

    per_file = {
        name: {
            "unittest": unittest_counts.get(name, 0),
            "pytest": pytest_counts.get(name, 0),
        }
        for name in files
    }

    uncollected = sorted(
        name
        for name, counts in per_file.items()
        if counts["unittest"] == 0 and counts["pytest"] == 0
    )
    pytest_only = sorted(
        name
        for name, counts in per_file.items()
        if counts["unittest"] == 0 and counts["pytest"] > 0
    )
    missing_critical = sorted(
        name for name in LIFECYCLE_CRITICAL if name not in per_file
    )
    empty_critical = sorted(
        name
        for name in LIFECYCLE_CRITICAL
        if name in per_file
        and per_file[name]["unittest"] == 0
        and per_file[name]["pytest"] == 0
    )

    return {
        "files": len(files),
        "per_file": per_file,
        "uncollected": uncollected,
        "pytest_only": pytest_only,
        "missing_critical": missing_critical,
        "empty_critical": empty_critical,
        "pytest_runner_required": bool(pytest_only),
    }


def _violations(report: dict[str, object], *, profile_runners: set[str]) -> list[str]:
    problems: list[str] = []
    for name in report["uncollected"]:  # type: ignore[index]
        problems.append(
            f"{name} is collected by no runner at all -- every assertion in it "
            "is dead. Give it unittest.TestCase classes or bare pytest "
            "functions, or delete the file."
        )
    for name in report["missing_critical"]:  # type: ignore[index]
        problems.append(
            f"{name} is named lifecycle-critical but no longer exists. Renaming "
            "or deleting a lifecycle suite must be deliberate: update "
            "LIFECYCLE_CRITICAL in this file and say why in the commit."
        )
    for name in report["empty_critical"]:  # type: ignore[index]
        problems.append(f"{name} is lifecycle-critical and collects zero tests.")
    if report["pytest_runner_required"] and "pytest" not in profile_runners:
        pytest_only = ", ".join(report["pytest_only"])  # type: ignore[arg-type]
        problems.append(
            "these files are collected only by pytest, and the profile runs no "
            f"pytest step, so their assertions never execute: {pytest_only}. "
            "This is the exact #621 defect. Add a pytest runner to the profile "
            "or convert the files to unittest.TestCase."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a test file no runner collects would ship green."
    )
    parser.add_argument(
        "--runner",
        action="append",
        default=None,
        help="A runner the calling profile executes (repeatable): unittest, pytest.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the raw report.")
    args = parser.parse_args(argv)

    # Default mirrors the #621 fast profile, which runs both unittest
    # (python-unittest, hostile-unittest) and pytest (hostile-pytest).
    runners = set(args.runner or ["unittest", "pytest"])

    report = collect_report()
    problems = _violations(report, profile_runners=runners)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        per_file = report["per_file"]  # type: ignore[index]
        total_u = sum(v["unittest"] for v in per_file.values())  # type: ignore[union-attr]
        total_p = sum(v["pytest"] for v in per_file.values())  # type: ignore[union-attr]
        print(
            f"test files: {report['files']} | "
            f"unittest-collected: {total_u} | pytest-collected: {total_p} | "
            f"pytest-only files: {len(report['pytest_only'])}"  # type: ignore[arg-type]
        )
        for name in report["pytest_only"]:  # type: ignore[index]
            counts = per_file[name]  # type: ignore[index]
            print(f"  pytest-only: {name} ({counts['pytest']} tests)")

    if problems:
        print("\ntest-collection contract violated:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nevery test file is collected by a runner this profile executes.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
