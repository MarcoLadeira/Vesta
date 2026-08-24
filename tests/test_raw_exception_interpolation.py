"""#622 AC9: raw exception interpolation cannot grow in protected modules.

The acceptance criterion is verbatim: "Static checks prevent new raw exception
interpolation in protected modules."

Note the word *new*. #622 lists the sinks that matter — "event journal;
ordinary logs; provider/tool result envelopes; cost/ledger notes; receipts;
GUI/CLI payloads; screenshots; crash reports; support bundles;
telemetry/export" — and a measurement across production code found **58**
places that interpolate a caught exception without passing it through a
redactor first. Eliminating all 58 is real work spread across seventeen
modules and several owners. Pretending otherwise, or deleting the criterion,
both lose. What is achievable now, and valuable on its own, is a ratchet:
the known number cannot silently grow while that work proceeds.

This is the same shape as ``tests/test_state_vocabulary_drift.py``, which
snapshots vocabularies it cannot yet unify, and for the same reason.

**What this file is not.** A green run here is not a statement that the
snapshotted modules are safe. It states only that they got no worse. The
counts below are a debt register, and the correct direction for every one of
them is down. Lowering a number is always allowed — the test tells you to
update the snapshot when you do, so progress is recorded rather than
silently reabsorbed.

**What counts as raw.** A ``str(exc)`` on the exception a handler bound,
where the value is not enclosed in a call to one of the known redactors
(``redact``, ``redact_secrets``, ``normalize_provider_error``, ...). Those
wrappers are the sanctioned boundary — see PR #677, which made
``opaihub.ask`` route both of its sites through ``redact`` and is why that
module no longer appears here at all.

**Known limitation, stated rather than papered over.** This detects the
direct ``str(exc)`` form. Binding the exception to a local first
(``detail = exc; f"{detail}"``) or using ``exc.args[0]`` or ``repr(exc)``
evades it. The contract is "the common form cannot grow unnoticed", not
"no leak is expressible".
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGES = ("opaihub", "opai")

# Calls that sanitise their argument. A ``str(exc)`` inside one of these is
# already at the boundary #622 asks for and is not counted.
REDACTORS = frozenset(
    {
        "redact",
        "redact_secrets",
        "redact_structure",
        "normalize_provider_error",
        "classify_error_code",
        "dedupe_error_text",
        "_policy_finding",
    }
)

# Raw-interpolation debt as measured on 2026-08-05, per module. Every number
# here is a defect budget, not an allowance to spend. Lower them.
SNAPSHOT: dict[str, int] = {
    "opai/cli.py": 13,
    "opai/cli_stream.py": 1,
    "opai/gui_desktop.py": 1,
    "opai/gui_web.py": 17,
    "opai/integrations.py": 2,
    "opaihub/accounts.py": 1,
    "opaihub/app_verify.py": 2,
    "opaihub/cli.py": 2,
    "opaihub/desktop_artifacts.py": 1,
    "opaihub/gui_pipeline.py": 3,
    "opaihub/repository_safety.py": 2,
    "opaihub/tool_health.py": 1,
    "opaihub/tool_loop.py": 2,
    "opaihub/verification_execution.py": 2,
    "opaihub/worktree_leases.py": 1,
}


def _count_str_of(node: ast.AST, bound: str) -> int:
    """How many ``str(<bound>)`` calls appear anywhere under ``node``."""

    total = 0
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "str"
            and len(sub.args) == 1
            and isinstance(sub.args[0], ast.Name)
            and sub.args[0].id == bound
        ):
            total += 1
    return total


class _RedactedCounter(ast.NodeVisitor):
    """Counts ``str(<bound>)`` occurrences that sit inside a redactor call."""

    def __init__(self, bound: str) -> None:
        self.bound = bound
        self.redacted = 0

    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in REDACTORS:
            # Everything below a redactor is sanitised; stop descending so a
            # nested str(exc) is not double-counted as raw.
            self.redacted += _count_str_of(node, self.bound)
            return
        self.generic_visit(node)


def raw_interpolations(source: str) -> int:
    """Unredacted ``str(<caught exception>)`` interpolations in one module."""

    tree = ast.parse(source)
    raw = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        total = _count_str_of(node, node.name)
        counter = _RedactedCounter(node.name)
        for statement in node.body:
            counter.visit(statement)
        raw += total - counter.redacted
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


class RawInterpolationRatchetTests(unittest.TestCase):
    def test_no_module_exceeds_its_recorded_debt(self) -> None:
        measured = measure_repository()
        grown = {
            module: (SNAPSHOT[module], count)
            for module, count in measured.items()
            if module in SNAPSHOT and count > SNAPSHOT[module]
        }
        self.assertEqual(
            grown,
            {},
            "raw exception interpolation grew (module: was -> now). Route the "
            "new one through redact()/normalize_provider_error() at the "
            "boundary — see #622 and opaihub/ask.py for the pattern.",
        )

    def test_no_new_module_starts_interpolating_raw_exceptions(self) -> None:
        measured = measure_repository()
        newcomers = sorted(set(measured) - set(SNAPSHOT))
        self.assertEqual(
            newcomers,
            [],
            "these modules newly interpolate a raw caught exception: "
            + ", ".join(newcomers)
            + " — redact at the boundary instead of adding to the debt",
        )

    def test_the_snapshot_has_not_silently_gone_stale(self) -> None:
        """A module that dropped to zero must leave the snapshot.

        Without this, fixing a module's last site would leave a stale entry
        that quietly re-permits the debt later.
        """
        measured = measure_repository()
        fixed = sorted(set(SNAPSHOT) - set(measured))
        self.assertEqual(
            fixed,
            [],
            "these modules no longer interpolate raw exceptions — remove them "
            "from SNAPSHOT to lock the improvement in: " + ", ".join(fixed),
        )

    def test_modules_that_improved_have_their_debt_lowered(self) -> None:
        measured = measure_repository()
        improved = {
            module: (SNAPSHOT[module], count)
            for module, count in measured.items()
            if module in SNAPSHOT and count < SNAPSHOT[module]
        }
        self.assertEqual(
            improved,
            {},
            "these modules improved (was -> now) — lower their SNAPSHOT entry "
            "so the gain cannot be silently given back",
        )


class DetectorTests(unittest.TestCase):
    """Without these, an analyser bug leaves a permanently-green test."""

    def test_a_raw_interpolation_is_counted(self) -> None:
        self.assertEqual(
            raw_interpolations(
                "try:\n"
                "    run()\n"
                "except Exception as exc:\n"
                "    return {'error': str(exc)}\n"
            ),
            1,
        )

    def test_a_redacted_interpolation_is_not_counted(self) -> None:
        self.assertEqual(
            raw_interpolations(
                "try:\n"
                "    run()\n"
                "except Exception as exc:\n"
                "    return {'error': redact(str(exc))}\n"
            ),
            0,
        )

    def test_a_normalised_provider_error_is_not_counted(self) -> None:
        self.assertEqual(
            raw_interpolations(
                "try:\n"
                "    run()\n"
                "except Exception as exc:\n"
                "    return normalize_provider_error('claude', str(exc))\n"
            ),
            0,
        )

    def test_an_unbound_handler_cannot_interpolate(self) -> None:
        self.assertEqual(
            raw_interpolations("try:\n    run()\nexcept Exception:\n    return None\n"),
            0,
        )

    def test_a_mixed_handler_counts_only_the_raw_one(self) -> None:
        self.assertEqual(
            raw_interpolations(
                "try:\n"
                "    run()\n"
                "except Exception as exc:\n"
                "    log(redact(str(exc)))\n"
                "    return {'error': str(exc)}\n"
            ),
            1,
        )

    def test_the_scan_actually_reaches_production_code(self) -> None:
        # A path typo would measure nothing and pass forever.
        measured = measure_repository()
        self.assertGreater(sum(measured.values()), 20)
        self.assertIn("opai/gui_web.py", measured)


if __name__ == "__main__":
    unittest.main()
