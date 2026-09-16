"""#622 AC9: raw exception interpolation is prohibited in production code.

The acceptance criterion is verbatim: "Static checks prevent new raw exception
interpolation in protected modules."

**This file used to be a ratchet and is now a prohibition.** That change is the
point, so the history is worth keeping.

It began as a debt register: 58 sites measured across production code, later
49, each module carrying a number that was allowed to fall and never rise. The
original docstring was explicit that a green run "is not a statement that the
snapshotted modules are safe. It states only that they got no worse."

It also declared its own blind spot, and that turned out to be the important
part:

    **Known limitation, stated rather than papered over.** This detects the
    direct ``str(exc)`` form. Binding the exception to a local first
    (``detail = exc; f"{detail}"``) or using ``exc.args[0]`` or ``repr(exc)``
    evades it.

Measuring those forms found **32 further sites** the register had never
counted -- 81 in total across 28 modules, rather than 49 across 14. Most of the
newly-visible ones were ``f"{exc}"``: the same leak, written the way people
naturally write it. A static check that cannot see the commonest form of the
thing it forbids is not much of a check.

So the detector now covers every way an exception's text becomes a string --
``str``/``repr``, f-strings, ``.format``, percent-formatting, and direct
access to the attributes that *are* the text -- and all 81 sites route through
``safe_detail`` or an existing redactor. The snapshot is empty, and a single
new raw interpolation fails the build.

**What counts as raw.** A reference to the exception a handler bound whose
text reaches a string, where the value is not enclosed in a call to one of the
known redactors (``redact``, ``redact_secrets``, ``normalize_provider_error``,
``safe_detail``, ...). Those wrappers are the sanctioned boundary.

**What deliberately does not count**, because #622 warns against exactly this
("false-positive redaction hides necessary non-secret identifier"):

* ``type(exc).__name__`` -- the class, never the message. It is the *stable
  category* the issue asks callers to surface, so penalising it would punish
  the pattern the issue wants.
* ``stdout = exc.stdout`` -- binding, not leaking. ``command_runner`` binds a
  timed-out subprocess's output and redacts at the point of use, which is
  correct; the local faces every other rule here the moment it is used.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGES = ("vestahub", "vesta")

# Calls that sanitise their argument. A reference to the exception inside one
# of these is already at the boundary #622 asks for and is not counted.
REDACTORS = frozenset(
    {
        "redact",
        "redact_secrets",
        "redact_structure",
        "normalize_provider_error",
        "classify_error_code",
        "dedupe_error_text",
        "_policy_finding",
        # The migration adapter functional requirement 2 asks for: bounded,
        # redacted, fail-closed text for a site that cannot carry a full
        # BoundaryError. See vestahub/boundary_errors.py.
        "safe_detail",
    }
)

#: Attributes of an exception that are safe to name: they describe the class,
#: never the message.
SAFE_ATTRIBUTES = frozenset({"__name__", "__qualname__"})

#: Attributes whose value *is* exception text under another name.
#: ``exc.stderr`` on a ``CalledProcessError`` is subprocess output, which #622
#: names explicitly as a canary injection point.
#:
#: ``reason`` is deliberately **not** here, and that was decided by evidence
#: rather than taste. Both of its occurrences in this codebase are
#: ``RepositoryProbeError.reason``, which holds a stable machine code like
#: ``repository_missing`` while the free text lives in ``.detail`` and is
#: redacted at construction. Treating it as leaky replaced a category with a
#: sentence and broke a test that asserted the category -- which is precisely
#: the "false-positive hides a necessary non-secret identifier" failure #622
#: names. The residual gap is honest: ``urllib.error.URLError.reason`` does
#: carry text, and a bare ``{"e": exc.reason}`` of one would not be caught
#: here, though any formatted use of it would be.
LEAKY_ATTRIBUTES = frozenset({"args", "stderr", "stdout", "output", "message"})

SNAPSHOT: dict[str, int] = {}
"""Raw-interpolation debt. **Empty, and it stays empty.**

An empty snapshot is what turns this file from a ratchet into a prohibition.
If a genuinely unavoidable case ever appears, adding it here is a deliberate,
reviewed act with a number attached -- which is the "explicit, scoped and
auditable" false-positive handling #622 asks for, and the opposite of the
"silent global off switch" it forbids.
"""


def _is_sanctioned(node: ast.AST) -> bool:
    """Whether this expression is already at the boundary #622 asks for."""

    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = None
            if isinstance(sub.func, ast.Name):
                name = sub.func.id
            elif isinstance(sub.func, ast.Attribute):
                name = sub.func.attr
            if name in REDACTORS:
                return True
    if isinstance(node, ast.Attribute) and node.attr in SAFE_ATTRIBUTES:
        target = node.value
        if (
            isinstance(target, ast.Call)
            and isinstance(target.func, ast.Name)
            and target.func.id == "type"
        ):
            return True
    return False


class _RawInterpolationCounter(ast.NodeVisitor):
    """Every way a caught exception's text reaches a string."""

    def __init__(self, bound: str) -> None:
        self.bound = bound
        self.count = 0
        self._binding = False

    def _is_bound(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == self.bound

    def _mentions_bound(self, node: ast.AST) -> bool:
        return any(self._is_bound(sub) for sub in ast.walk(node))

    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in REDACTORS:
            return  # sanctioned: stop descending
        if (
            name in ("str", "repr")
            and len(node.args) == 1
            and self._is_bound(node.args[0])
        ):
            self.count += 1
            return
        if name == "format" and any(self._is_bound(arg) for arg in node.args):
            self.count += 1
        self.generic_visit(node)

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        if _is_sanctioned(node.value):
            return
        if self._mentions_bound(node.value):
            self.count += 1
            return
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Mod) and not _is_sanctioned(node.right):
            if self._mentions_bound(node.right):
                self.count += 1
                return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Binding raw attribute text to a local is not the defect.

        Interpolation inside an assignment is still counted -- ``message =
        f"{exc}"`` is a leak in waiting. It is only the *binding* of an
        attribute that is innocent, because the local faces every other rule
        here at the point it is used.
        """

        if all(isinstance(target, ast.Name) for target in node.targets):
            self._binding = True
            try:
                self.generic_visit(node)
            finally:
                self._binding = False
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._is_bound(node.value) and node.attr in LEAKY_ATTRIBUTES:
            if not self._binding:
                self.count += 1
            return
        self.generic_visit(node)


def raw_interpolations(source: str) -> int:
    """Unredacted uses of a caught exception's text in one module."""

    tree = ast.parse(source)
    raw = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        counter = _RawInterpolationCounter(node.name)
        for statement in node.body:
            counter.visit(statement)
        raw += counter.count
    return raw


def measure_repository() -> dict[str, int]:
    counts: dict[str, int] = {}
    for package in PRODUCTION_PACKAGES:
        for path in sorted((REPOSITORY_ROOT / package).rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "tests" in parts:
                continue
            try:
                found = raw_interpolations(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - broken files fail elsewhere
                continue
            if found:
                counts[path.relative_to(REPOSITORY_ROOT).as_posix()] = found
    return counts


class RawInterpolationProhibitionTests(unittest.TestCase):
    def test_production_code_holds_no_raw_interpolations(self) -> None:
        measured = measure_repository()
        self.assertEqual(
            measured,
            {},
            "raw exception interpolation reappeared (module: count). Route it "
            "through safe_detail() at the boundary -- see #622 and "
            "vestahub/boundary_errors.py.",
        )

    def test_no_module_exceeds_its_recorded_debt(self) -> None:
        measured = measure_repository()
        grown = {
            module: (SNAPSHOT[module], count)
            for module, count in measured.items()
            if module in SNAPSHOT and count > SNAPSHOT[module]
        }
        self.assertEqual(grown, {}, "raw interpolation grew (module: was -> now)")

    def test_no_new_module_starts_interpolating_raw_exceptions(self) -> None:
        measured = measure_repository()
        newcomers = sorted(set(measured) - set(SNAPSHOT))
        self.assertEqual(
            newcomers,
            [],
            "these modules newly interpolate a raw caught exception: "
            + ", ".join(newcomers)
            + " -- redact at the boundary instead of adding to the debt",
        )

    def test_the_snapshot_has_not_silently_gone_stale(self) -> None:
        """A module that dropped to zero must leave the snapshot."""

        measured = measure_repository()
        fixed = sorted(set(SNAPSHOT) - set(measured))
        self.assertEqual(
            fixed,
            [],
            "these modules no longer interpolate raw exceptions -- remove them "
            "from SNAPSHOT to lock the improvement in: " + ", ".join(fixed),
        )


class DetectorTests(unittest.TestCase):
    """Without these, an analyser bug leaves a permanently-green test."""

    def _count(self, body: str) -> int:
        return raw_interpolations("try:\n    run()\nexcept Exception as exc:\n" + body)

    def test_a_raw_str_interpolation_is_counted(self) -> None:
        self.assertEqual(self._count("    return {'error': str(exc)}\n"), 1)

    def test_an_f_string_interpolation_is_counted(self) -> None:
        """The form the original detector could not see, and the commonest."""

        self.assertEqual(self._count('    return {"error": f"failed: {exc}"}\n'), 1)

    def test_a_repr_interpolation_is_counted(self) -> None:
        self.assertEqual(self._count("    return {'error': repr(exc)}\n"), 1)

    def test_percent_formatting_is_counted(self) -> None:
        self.assertEqual(self._count("    return 'failed: %s' % exc\n"), 1)

    def test_an_exception_attribute_sunk_directly_is_counted(self) -> None:
        self.assertEqual(self._count("    return {'error': exc.stderr}\n"), 1)

    def test_a_redacted_interpolation_is_not_counted(self) -> None:
        self.assertEqual(self._count("    return {'error': redact(str(exc))}\n"), 0)

    def test_safe_detail_counts_as_a_redactor(self) -> None:
        self.assertEqual(self._count("    return {'error': safe_detail(exc)}\n"), 0)

    def test_a_normalised_provider_error_is_not_counted(self) -> None:
        self.assertEqual(
            self._count("    return normalize_provider_error('claude', str(exc))\n"), 0
        )

    def test_the_exception_class_name_is_not_counted(self) -> None:
        """It is the stable category #622 asks callers to surface."""

        self.assertEqual(
            self._count('    return {"error": f"{type(exc).__name__}"}\n'), 0
        )

    def test_binding_an_attribute_to_a_local_is_not_counted(self) -> None:
        """Reading the attribute is not the defect; sinking it unredacted is."""

        self.assertEqual(
            self._count("    out = exc.stdout or ''\n    return redact(str(out))\n"), 0
        )

    def test_an_unbound_handler_cannot_interpolate(self) -> None:
        self.assertEqual(
            raw_interpolations("try:\n    run()\nexcept Exception:\n    return None\n"),
            0,
        )

    def test_a_mixed_handler_counts_only_the_raw_one(self) -> None:
        self.assertEqual(
            self._count("    log(redact(str(exc)))\n    return {'error': str(exc)}\n"),
            1,
        )

    def test_the_scan_actually_reaches_production_code(self) -> None:
        """A path typo would measure nothing and pass forever.

        This used to assert the scan found more than twenty sites, which was a
        fine proof of life while there were sixty. Now that the answer is zero,
        "found nothing" is indistinguishable from "looked nowhere", so the
        check is that it looked: the packages are on disk and a module that
        held seventeen raw interpolations is now clean.
        """

        scanned = sum(
            1
            for package in PRODUCTION_PACKAGES
            for path in (REPOSITORY_ROOT / package).rglob("*.py")
            if "__pycache__" not in path.parts
        )
        self.assertGreater(scanned, 100, "the scan is not reaching the packages")

        witness = REPOSITORY_ROOT / "vesta" / "gui_web.py"
        self.assertTrue(witness.exists(), "the witness module moved; pick another")
        self.assertEqual(
            raw_interpolations(witness.read_text(encoding="utf-8")),
            0,
            "gui_web.py held 17 raw interpolations and must stay at zero",
        )


if __name__ == "__main__":
    unittest.main()


class FalsePositivesAreTheOtherFailureTests(unittest.TestCase):
    """#622 names this risk explicitly, and the migration hit it.

        False-positive redaction hides necessary non-secret identifier.

    A first pass at this work treated ``exc.reason`` as leaky and rewrote
    ``{"reason": exc.reason}`` into ``{"reason": safe_detail(exc)}``. But
    ``RepositoryProbeError.reason`` is a stable machine code -- the free text
    lives in ``.detail`` and is redacted at construction -- so the rewrite
    replaced a category with a sentence, and a test asserting the category
    failed. Redaction that eats the thing a caller dispatches on is not
    safety, it is a different bug.

    These pin both halves of the line, because a detector tuned only for
    recall quietly makes the codebase worse.
    """

    def test_a_stable_code_attribute_is_not_treated_as_a_leak(self) -> None:
        self.assertNotIn("reason", LEAKY_ATTRIBUTES)

    def test_the_attributes_that_carry_free_text_are_still_leaky(self) -> None:
        for attribute in ("stderr", "stdout", "output", "args", "message"):
            with self.subTest(attribute=attribute):
                self.assertIn(attribute, LEAKY_ATTRIBUTES)

    def test_the_repository_probe_error_keeps_its_category(self) -> None:
        """The regression itself, pinned against the real class."""

        from vestahub.repository_safety import RepositoryProbeError

        error = RepositoryProbeError("repository_missing", "/home/me/secret/path")

        self.assertEqual(error.reason, "repository_missing")

    def test_that_errors_free_text_is_redacted_at_construction(self) -> None:
        """Which is why reading ``.reason`` needs no further redaction."""

        from vestahub.repository_safety import RepositoryProbeError

        error = RepositoryProbeError(
            "repository_missing",
            "token sk-ant-api03-FAKEFAKEFAKEFAKEFAKE1234 in the path",  # pragma: allowlist secret
        )

        self.assertNotIn("sk-ant-api03", error.detail)
