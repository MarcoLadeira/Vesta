"""#618: structural proof that no surface can decide a terminal result itself.

The adoption guard (test_run_result_adoption.py) proves the projector has a
production caller. That is necessary and not sufficient: a projector can be
called and then ignored, which is roughly what was happening before this issue
-- every surface held its own mapping table and its own legacy fallbacks, and
the same evidence could read `complete` in history and `partial` in the GUI.

These tests prove the other half: the surfaces that report a finished run read
the canonical result, and the legacy vocabulary cannot reach a success verdict
from anywhere.

Deliberately AST-based. A substring search cannot tell `"answered_by_account"`
appearing in a comparison that decides completion from the same string sitting
in a compatibility mapping table, and the difference is the entire point.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

#: Surfaces that report what a finished run meant. Each must consult the
#: canonical result rather than reach its own verdict.
RESULT_SURFACES = {
    "vesta/gui_recents.py": "history / recents",
    "vesta/gui_web.py": "GUI",
    "vesta/cli_stream.py": "CLI",
    "vestahub/background_runs.py": "background runs",
}

#: The legacy vocabulary that used to mean "this run succeeded".
LEGACY_SUCCESS_TOKENS = frozenset(
    {
        "answered",
        "answered_by_account",
        "answered_by_free_api",
        "answered_by_paid_api",
        "answered_locally",
        "cache_hit",
        "applied",
        "no_edits",
    }
)

#: The canonical downstream accessors. `terminal_presentation` is the one
#: authority (#618, after #698); the others are the module-local paths that end
#: at it -- the shared history helper, and validating a payload into a
#: RunResult before asking. A surface must reach one of these.
CANONICAL_ACCESSORS = frozenset(
    {
        "terminal_presentation",
        "thread_status_for_result",
        "_canonical_result",
        "from_dict",
    }
)

#: Canonical states a legacy comparison must never be able to produce.
FORBIDDEN_RESULTS = frozenset({"complete", "completed", "verified", "success"})

#: Modules allowed to name legacy tokens next to canonical states, because
#: converting between the two vocabularies is precisely their job.
COMPATIBILITY_MODULES = frozenset(
    {
        "vestahub/legacy_status.py",
        "vestahub/legacy_alias_telemetry.py",
        "vestahub/generated_lifecycle.py",
    }
)


def _module_source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.AST:
    return ast.parse(_module_source(relative))


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


class SurfacesConsultTheCanonicalResultTests(unittest.TestCase):
    def test_every_result_surface_reads_the_canonical_result(self) -> None:
        """Each reporting surface must reach the canonical accessor.

        `terminal_presentation` is the one downstream accessor (#618, after
        #698). A surface may reach it directly, through the shared history
        helper that does (`thread_status_for_result`), or by validating the
        payload into a RunResult first -- all end at the same authority. What
        is not allowed is reporting a finished run without reaching it.
        """

        for relative, label in sorted(RESULT_SURFACES.items()):
            with self.subTest(surface=label):
                called = _called_names(_tree(relative))
                self.assertTrue(
                    CANONICAL_ACCESSORS & called,
                    f"{label} ({relative}) reports finished runs without "
                    "consulting the canonical RunResult. Read it via "
                    "terminal_presentation() instead of deriving one here.",
                )

    def test_no_surface_reintroduces_a_competing_accessor(self) -> None:
        """One accessor, not two.

        A previous revision of this branch added `canonical_run_state`
        alongside `terminal_presentation`. Two ways to ask what a run meant is
        the same defect as two ways to decide it, one indirection later, so the
        competing accessor was removed rather than kept as an alternative.
        """

        for relative in sorted(RESULT_SURFACES):
            with self.subTest(surface=relative):
                self.assertNotIn(
                    "canonical_run_state",
                    _module_source(relative),
                    "a second canonical accessor is back; consumers must go "
                    "through terminal_presentation",
                )

    def test_the_surface_list_is_not_silently_empty(self) -> None:
        """A guard that checks nothing passes forever."""

        self.assertGreaterEqual(len(RESULT_SURFACES), 4)
        for relative in RESULT_SURFACES:
            with self.subTest(module=relative):
                self.assertTrue((ROOT / relative).is_file(), f"{relative} moved")


class NoLegacyStringDecidesSuccessTests(unittest.TestCase):
    """A legacy status may narrow an unknown result. It may not report success.

    This is the invariant behind the two production defects #618 fixed: history
    turned `answered_by_account` into `complete`, and background runs turned
    `{"status": "answered"}` into COMPLETED. Both were single comparisons, and
    both looked entirely reasonable in isolation.
    """

    def _string_constants(self, node: ast.AST) -> set[str]:
        found: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.add(child.value.strip().lower())
        return found

    def test_no_surface_returns_a_success_state_from_a_legacy_comparison(self) -> None:
        offenders: list[str] = []
        for relative in sorted(RESULT_SURFACES):
            tree = _tree(relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                tested = self._string_constants(node.test)
                if not (tested & LEGACY_SUCCESS_TOKENS):
                    continue
                # The body of a branch keyed on legacy success vocabulary must
                # not yield a canonical success. Returning "needs_attention",
                # raising, or recording telemetry is all fine.
                produced = set()
                for statement in node.body:
                    produced |= self._string_constants(statement)
                leaked = produced & FORBIDDEN_RESULTS
                if leaked:
                    offenders.append(
                        f"{relative}:{node.lineno} branches on "
                        f"{sorted(tested & LEGACY_SUCCESS_TOKENS)} and yields "
                        f"{sorted(leaked)}"
                    )
        self.assertEqual(
            offenders,
            [],
            "a legacy status string decides success again:\n  "
            + "\n  ".join(offenders)
            + "\nA legacy value records that the provider replied. That is "
            "transport, not engineering completion -- import it as "
            "needs_attention and let the canonical result decide.",
        )

    def test_the_detector_catches_a_reintroduced_legacy_success(self) -> None:
        """Prove the guard above has teeth rather than trusting it.

        Without this, a bug in the matcher would make the previous test pass on
        every possible input, which is the failure mode static guards actually
        have in practice.
        """

        reintroduced = ast.parse(
            "def finish(status):\n"
            "    if status == 'answered_by_account':\n"
            "        return 'complete'\n"
            "    return 'failed'\n"
        )
        caught = False
        for node in ast.walk(reintroduced):
            if not isinstance(node, ast.If):
                continue
            tested = self._string_constants(node.test)
            if not (tested & LEGACY_SUCCESS_TOKENS):
                continue
            produced: set[str] = set()
            for statement in node.body:
                produced |= self._string_constants(statement)
            if produced & FORBIDDEN_RESULTS:
                caught = True
        self.assertTrue(caught, "the legacy-success detector does not detect")


class NoSecondRetryStateListTests(unittest.TestCase):
    """Retry eligibility is generated from the schema; nobody restates it.

    `_AUTOMATIC_RETRY_STATES` used to be a hand-written frozenset. #618 made it
    a generated per-state field, and this stops a replacement appearing beside
    it under a different name.
    """

    def test_no_module_hand_writes_a_retry_state_set(self) -> None:
        offenders: list[str] = []
        for package in ("vesta", "vestahub", "opcoding"):
            for path in sorted((ROOT / package).rglob("*.py")):
                relative = path.relative_to(ROOT).as_posix()
                if "__pycache__" in relative or relative in COMPATIBILITY_MODULES:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (OSError, SyntaxError):  # pragma: no cover - defensive
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Assign):
                        continue
                    targets = {
                        t.id.lower() for t in node.targets if isinstance(t, ast.Name)
                    }
                    if not any("retry" in name for name in targets):
                        continue
                    literals = {
                        child.value.strip().lower()
                        for child in ast.walk(node.value)
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                    }
                    # A set naming two or more terminal lifecycle states is a
                    # retry-state table, whatever it is called.
                    if len(literals & {"failed", "timeout", "partial", "blocked"}) >= 2:
                        offenders.append(f"{relative}:{node.lineno} {sorted(targets)}")
        self.assertEqual(
            offenders,
            [],
            "a hand-maintained retry-state list is back:\n  "
            + "\n  ".join(offenders)
            + "\nRetry eligibility is automatic_retry_eligible in "
            "lifecycle_schema.json; derive it from STATE_SPECS.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
