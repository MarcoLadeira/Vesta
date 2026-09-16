"""#618: the canonical RunResult projection must have real production callers.

Vesta has now built a correct primitive and left it unadopted at least eight
times. `project_run_result` was the most expensive instance: a versioned,
evidence-derived, provider-neutral result envelope with its own tests, which no
production path ever called. Every surface -- GUI, CLI, background runs,
history, receipts -- kept deriving its own terminal meaning from the verdict
dict and the legacy status string, so the same evidence could read `completed`
on one surface and `answered_by_account` on another.

A passing unit test suite cannot detect that: the primitive's own tests were
green the entire time it was dead. The defect is not in the primitive, it is in
the *call graph*, so the guard has to look at the call graph.

This is deliberately an import/call-graph check rather than a grep. A grep for
the name matches its own definition, its docstring, a comment mentioning it,
and an import that is never called -- all of which were true while adoption was
zero.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

#: Packages that ship to users. `tests/` and `scripts/` are excluded on
#: purpose: a test calling the projection is exactly the false green this guard
#: exists to reject.
PRODUCTION_PACKAGES = ("vesta", "vestahub", "opcoding")

#: The module that defines the projection. Its own internal use is not
#: adoption, so it can never satisfy the guard on its own.
DEFINING_MODULE = "vestahub/run_result_projection.py"

CANONICAL_BUILDERS = ("project_run_result", "project_run_result_for_background_run")


def _production_files() -> list[Path]:
    files: list[Path] = []
    for package in PRODUCTION_PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative == DEFINING_MODULE:
                continue
            if "__pycache__" in relative:
                continue
            files.append(path)
    return files


def _called_names(tree: ast.AST) -> set[str]:
    """Names invoked as calls, whether bare or through an attribute."""

    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


class CanonicalRunResultAdoptionTests(unittest.TestCase):
    def test_the_canonical_projection_has_a_production_caller(self) -> None:
        callers: dict[str, list[str]] = {name: [] for name in CANONICAL_BUILDERS}
        for path in _production_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # pragma: no cover - defensive
                continue
            called = _called_names(tree)
            for name in CANONICAL_BUILDERS:
                if name in called:
                    callers[name].append(path.relative_to(ROOT).as_posix())

        adopted = [name for name, sites in callers.items() if sites]
        self.assertTrue(
            adopted,
            "No production module calls the canonical RunResult projection. "
            "The primitive is built and tested but dead, which is the exact "
            "state #618 exists to end: every surface then re-derives its own "
            "terminal meaning. Call project_run_result from the production "
            "path that holds the CompletionVerdictResult -- do not delete or "
            "weaken this guard to make it pass.",
        )

    def test_adoption_is_not_satisfied_by_the_defining_module_alone(self) -> None:
        """The projection's own internal use must not count as adoption.

        Without this, moving a call inside run_result_projection.py would turn
        the guard above green while production remained exactly as dead as
        before.
        """

        scanned = {path.relative_to(ROOT).as_posix() for path in _production_files()}
        self.assertNotIn(DEFINING_MODULE, scanned)

    def test_the_guard_scans_a_plausible_number_of_modules(self) -> None:
        """A guard that silently scans nothing passes forever.

        If the package layout moves and the globs stop matching, every
        assertion above becomes vacuously true. Assert the sweep is real.
        """

        self.assertGreater(
            len(_production_files()),
            50,
            "The production sweep collapsed to almost nothing -- the guard is "
            "no longer checking what it claims to check.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
