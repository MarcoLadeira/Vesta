"""#617 AC10: exception-driven dispatch fallback cannot come back.

The issue's acceptance criteria include, verbatim: "Static/architecture tests
prevent reintroduction of exception-driven dispatch fallback." PR #676
removed the two live instances; this file is what stops a third appearing.

The banned shape is a provider/tool call wrapped in ``except TypeError``
whose handler decides what to do by *reading the exception's message text*
for an argument name, then calls again:

    try:
        return runner.complete(text, on_text=on_text)   # may already have
    except TypeError as exc:                            # reached the provider
        if "on_text" not in str(exc):                   # <-- text sniffing
            raise
        return runner.complete(text)                    # <-- second dispatch

It is unsafe because the first call may already have crossed the provider
boundary — real cost, real streamed output, a real subprocess — before an
*unrelated* internal ``TypeError`` was raised. If that exception's message
merely happens to mention the argument name, the retry silently dispatches
the same operation twice. Capability must be decided from the signature
before dispatch (``vestahub.ask._supports_kwarg``), never from an exception
after it.

Detection is AST-based, not textual: it looks for a membership test against
``str(<the caught exception>)`` inside a ``TypeError`` handler. That is the
*sniffing* half of the pattern, which is what makes it dangerous and what no
legitimate code in this repo needs. Matching on ``except TypeError`` alone
would be useless — the repo has several legitimate ones validating input
(``iter()`` on a non-iterable, ``inspect.signature`` on a C callable).

Known limitation, stated rather than papered over: this catches the pattern
in the form it has actually appeared. A determined rewrite (assigning
``str(exc)`` to a local first, or using ``.args[0]``) would evade it. The
honest contract is "the mistake we made twice cannot be made the same way a
third time", not "no unsafe retry is expressible".
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGES = ("vestahub", "vesta")


def _production_sources() -> list[Path]:
    files: list[Path] = []
    for package in PRODUCTION_PACKAGES:
        for path in sorted((REPOSITORY_ROOT / package).rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "tests" in parts:
                continue
            files.append(path)
    return files


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    """Every exception type name this handler catches."""

    node = handler.type
    if node is None:
        return set()
    candidates = node.elts if isinstance(node, ast.Tuple) else [node]
    names: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, ast.Name):
            names.add(candidate.id)
        elif isinstance(candidate, ast.Attribute):
            names.add(candidate.attr)
    return names


def _sniffs_exception_text(handler: ast.ExceptHandler) -> bool:
    """True when the handler branches on ``... in str(<caught exception>)``."""

    bound = handler.name
    if not bound:
        return False
    for node in ast.walk(handler):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        for operand in [node.left, *node.comparators]:
            if (
                isinstance(operand, ast.Call)
                and isinstance(operand.func, ast.Name)
                and operand.func.id == "str"
                and len(operand.args) == 1
                and isinstance(operand.args[0], ast.Name)
                and operand.args[0].id == bound
            ):
                return True
    return False


class ExceptionDrivenDispatchTests(unittest.TestCase):
    def test_no_typeerror_handler_branches_on_exception_text(self) -> None:
        offenders: list[str] = []
        for path in _production_sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if "TypeError" not in _caught_names(node):
                    continue
                if _sniffs_exception_text(node):
                    rel = path.relative_to(REPOSITORY_ROOT).as_posix()
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "exception-driven dispatch fallback reintroduced at: "
            + ", ".join(offenders)
            + " — decide capability from inspect.signature before dispatch "
            "(see vestahub.ask._supports_kwarg), not from exception text after it",
        )

    def test_the_detector_actually_detects_the_banned_shape(self) -> None:
        """Guard against the scan above passing because it detects nothing.

        Without this, deleting the body of ``_sniffs_exception_text`` would
        leave a permanently-green test that protects nothing.
        """
        banned = ast.parse(
            "try:\n"
            "    runner.complete(text, on_text=on_text)\n"
            "except TypeError as exc:\n"
            "    if 'on_text' not in str(exc):\n"
            "        raise\n"
            "    runner.complete(text)\n"
        )
        handlers = [n for n in ast.walk(banned) if isinstance(n, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIn("TypeError", _caught_names(handlers[0]))
        self.assertTrue(_sniffs_exception_text(handlers[0]))

    def test_the_detector_does_not_flag_legitimate_typeerror_handling(self) -> None:
        """Input validation that merely catches TypeError is fine and common."""
        legitimate = ast.parse(
            "try:\n"
            "    iterator = iter(events)\n"
            "except TypeError as exc:\n"
            "    raise ProtocolViolation('events must be iterable') from exc\n"
        )
        handlers = [n for n in ast.walk(legitimate) if isinstance(n, ast.ExceptHandler)]
        self.assertFalse(_sniffs_exception_text(handlers[0]))

    def test_the_scan_actually_reaches_production_code(self) -> None:
        # A path typo would silently scan nothing and pass forever.
        sources = _production_sources()
        self.assertGreater(len(sources), 50)
        names = {path.name for path in sources}
        self.assertIn("ask.py", names)
        self.assertIn("app_state.py", names)


if __name__ == "__main__":
    unittest.main()
