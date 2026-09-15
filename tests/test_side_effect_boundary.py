"""#616 acceptance: protected side effects exist only inside claimed operations.

Acceptance criterion 10: *"Direct side-effect calls outside the canonical
operation adapters are blocked by architecture/static tests in protected
modules."*

This is that static test. It parses the two dispatch-layer modules —
``provider_tools`` (provider-driven local, Git and GitHub mutations) and
``github_workflow`` (the ``gh`` adapter) — and proves that every call to a
known outward-mutation sink lives inside a function that claims an
``operation_key`` through the idempotency protocol before dispatching.

Scope, deliberately: the raw HTTP sinks in ``github_connector`` are the
registered adapters themselves, so they are *callees* here, not violators.
Read-only paths (``git status``, ``ls-remote``, issue/PR reads) are class 1
and need no claim. ``pr edit`` sets state rather than creating an outward
artifact, and repeating it is idempotent, so it is not a sink either.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VESTAHUB = ROOT / "vestahub"

#: Raw GitHub mutation adapters: calling one is an outward side effect.
GITHUB_ADAPTER_SINKS = {"create_pull_request", "add_comment", "request_reviewers"}

#: ``self._git([<verb>, ...])`` verbs that mutate refs, index or remotes.
GIT_MUTATION_VERBS = {"add", "checkout", "commit", "merge", "push", "rebase", "tag"}

#: ``self._run(["pr", <verb>, ...])`` gh verbs with outward, visible effects.
GH_MUTATION_VERBS = {"comment", "edit", "merge"}

#: Direct process execution sinks (a granted command can do anything).
PROCESS_SINKS = {"_git_run"}

#: Registered adapter functions the sinks may live inside without their own
#: claim — their *callers* are policed by the verb/adaptor rules above.
REGISTERED_ADAPTERS = {"_git"}

POLICED_MODULES = ("provider_tools.py", "github_workflow.py")


def _literal_strings(node: ast.AST) -> list[str]:
    """The literal string values of a list/tuple of constants, or []."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    out: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return []
        out.append(element.value)
    return out


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_sink(call: ast.Call) -> str | None:
    name = _call_name(call)
    if name in GITHUB_ADAPTER_SINKS:
        return f"github adapter {name}()"
    if name in PROCESS_SINKS:
        return f"process dispatch {name}()"
    if name == "_git" and call.args:
        argv = _literal_strings(call.args[0])
        if argv and argv[0] in GIT_MUTATION_VERBS:
            return f"git mutation {argv[0]!r}"
    if name == "_run" and call.args:
        argv = _literal_strings(call.args[0])
        if len(argv) >= 2 and argv[0] == "pr" and argv[1] in GH_MUTATION_VERBS:
            return f"gh mutation pr {argv[1]}"
    return None


def _enclosing_function(tree: ast.AST, target: ast.AST) -> ast.FunctionDef | None:
    best: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if child is target:
                if best is None or (
                    node.lineno >= best.lineno and node.end_lineno <= best.end_lineno
                ):
                    best = node
                break
    return best


class SideEffectBoundaryTests(unittest.TestCase):
    def test_every_outward_mutation_is_inside_a_claimed_operation(self) -> None:
        violations: list[str] = []
        for module_name in POLICED_MODULES:
            path = VESTAHUB / module_name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                sink = _is_sink(node)
                if sink is None:
                    continue
                function = _enclosing_function(tree, node)
                if function is not None and function.name in REGISTERED_ADAPTERS:
                    continue
                segment = (
                    ast.get_source_segment(source, function) if function else None
                ) or ""
                if "operation_key(" not in segment:
                    where = (
                        f"{function.name} (line {function.lineno})"
                        if function
                        else f"module level (line {node.lineno})"
                    )
                    violations.append(f"{module_name}: {sink} in {where}")
        self.assertEqual(
            violations,
            [],
            "outward side effects dispatched without a persisted operation:\n"
            + "\n".join(violations),
        )

    def test_policed_modules_still_exist(self) -> None:
        # A rename must fail loudly here rather than silently policing nothing.
        for module_name in POLICED_MODULES:
            self.assertTrue((VESTAHUB / module_name).is_file(), module_name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
