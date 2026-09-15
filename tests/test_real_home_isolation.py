"""Tests must never install Vesta's global integrations into the real home.

A full ``python -m unittest discover -s tests`` run kept rewriting the
developer's real ``~/.claude/CLAUDE.md``, ``~/.vesta/{status.txt,global.json,
instructions,integrations,bin,journal}`` and ``~/.agents/skills/vesta``. The
culprit was ``VisibilityTests.test_activate_repair_writes_visibility_files``
in ``test_obvious_on.py``: it ran ``vesta activate --repair`` in-process with
no hermetic home. ``cmd_activate`` passes ``install_global=True`` by default
and no ``home``, so ``activate_project`` -> ``install_global_integrations``
resolved ``Path.home()`` to the real profile. Sibling tests called
``activate_project``/``install_project`` without a ``home`` too; with global
integrations off that still resolves the real home and lets
``ensure_superpowers_bridge`` create ``~/.agents/skills/superpowers`` there.

Two guards keep it from coming back, both runnable under ``unittest`` (the
local CI gate ignores ``conftest.py``):

* a static ratchet: every home-resolving call in ``tests/test_*.py`` passes an
  explicit ``home``, and every in-process or subprocess CLI activation runs
  inside ``isolated_home()``;
* a runtime check that the exact call path which leaked now writes only under
  the throwaway home.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import isolated_home
from vesta import installer, integrations
from vesta.cli import main

TESTS = Path(__file__).resolve().parent

#: The home the test process started with -- captured at import, before any
#: test patches the environment. Under a normal developer run this is the real
#: profile; nothing a test does may resolve to it.
PROCESS_HOME = Path.home().resolve()

#: Functions that resolve ``Path.home()`` when ``home`` is omitted and can then
#: write home-level discovery files.
HOME_RESOLVING = {
    "activate_project": integrations.activate_project,
    "install_global_integrations": integrations.install_global_integrations,
    "ensure_superpowers_bridge": integrations.ensure_superpowers_bridge,
    "ensure_vesta_skill_library": integrations.ensure_vesta_skill_library,
    "uninstall_vesta": integrations.uninstall_vesta,
    "update_vesta_source": integrations.update_vesta_source,
    "install_project": installer.install_project,
}

#: CLI subcommands that activate a project or install global integrations and
#: take their home from the process environment.
ACTIVATING_SUBCOMMANDS = {"activate", "install", "integrate", "launch", "quickstart"}

ISOLATION_MARKER = "isolated_home("


def _func_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_dry_run(node: ast.Call) -> bool:
    return any(
        kw.arg == "dry_run"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in node.keywords
    )


def _passes_home(node: ast.Call, name: str) -> bool:
    if any(kw.arg == "home" for kw in node.keywords):
        return True
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        return False
    parameters = list(inspect.signature(HOME_RESOLVING[name]).parameters)
    return "home" in parameters[: len(node.args)]


def _string_items(node: ast.AST) -> list[str | None]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    return [
        elt.value
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        else None
        for elt in node.elts
    ]


def _cli_subcommand(node: ast.Call) -> str | None:
    """The activating subcommand an in-process ``main([...])`` call runs."""
    if _func_name(node) != "main" or not node.args:
        return None
    items = _string_items(node.args[0])
    if items and items[0] in ACTIVATING_SUBCOMMANDS and "--home" not in items:
        return items[0]
    return None


def _subprocess_subcommand(node: ast.AST) -> str | None:
    """The activating subcommand a ``[python, "-m", "vesta", ...]`` argv runs."""
    items = _string_items(node)
    for index in range(len(items) - 2):
        if (
            items[index] == "-m"
            and items[index + 1] == "vesta"
            and items[index + 2] in ACTIVATING_SUBCOMMANDS
            and "--home" not in items
        ):
            return items[index + 2]
    return None


class _Module:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.source = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(path))
        self.parents: dict[ast.AST, ast.AST] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
            if isinstance(parent, ast.ClassDef):
                self.classes[parent.name] = parent

    def _segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or ""

    def _class_isolates(self, cls: ast.ClassDef, seen: set[str]) -> bool:
        if cls.name in seen:
            return False
        seen.add(cls.name)
        if ISOLATION_MARKER in self._segment(cls):
            return True
        for base in cls.bases:
            if isinstance(base, ast.Name) and base.id in self.classes:
                if self._class_isolates(self.classes[base.id], seen):
                    return True
        return False

    def isolated(self, node: ast.AST) -> bool:
        """True when an enclosing function or class runs under isolated_home."""
        current = self.parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ISOLATION_MARKER in self._segment(current):
                    return True
            if isinstance(current, ast.ClassDef):
                if self._class_isolates(current, set()):
                    return True
            current = self.parents.get(current)
        return False


def home_isolation_violations(tests_dir: Path = TESTS) -> list[str]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        module = _Module(path)
        for node in ast.walk(module.tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Call):
                name = _func_name(node)
                if (
                    name in HOME_RESOLVING
                    and not _is_dry_run(node)
                    and not _passes_home(node, name)
                ):
                    found.append(
                        (path.name, line, f"{name}(...) without an explicit home=")
                    )
                    continue
                subcommand = _cli_subcommand(node)
                if subcommand and not module.isolated(node):
                    found.append(
                        (
                            path.name,
                            line,
                            f"main([{subcommand!r}, ...]) outside isolated_home()",
                        )
                    )
            subcommand = _subprocess_subcommand(node)
            if subcommand and not module.isolated(node):
                found.append(
                    (
                        path.name,
                        line,
                        f"`-m vesta {subcommand}` subprocess outside isolated_home()",
                    )
                )
    return [f"{name}:{line} {message}" for name, line, message in sorted(found)]


class StaticHomeIsolationRatchetTests(unittest.TestCase):
    def test_every_home_resolving_test_call_is_hermetic(self):
        violations = home_isolation_violations()
        self.assertEqual(
            violations,
            [],
            "These test calls resolve the developer's real home and can install "
            "Vesta's global integrations into it. Pass home=<tempdir> or run the "
            "CLI inside `with isolated_home():`\n  " + "\n  ".join(violations),
        )

    def test_ratchet_catches_the_original_leak(self):
        leaky = """
import unittest
from vesta.cli import main
from vesta.installer import install_project
from vesta.integrations import activate_project


class VisibilityTests(unittest.TestCase):
    def test_activate_repair(self):
        main(["activate", "--repair", "--project", "x"])
        activate_project(root, install_global=False)
        install_project(root, install_tools=False)
        subprocess.run([sys.executable, "-m", "vesta", "activate"])

    def test_dry_run_and_explicit_homes_are_fine(self):
        activate_project(root, install_global=False, dry_run=True)
        activate_project(root, home=home)
        integrations.ensure_vesta_skill_library(root, home)
        main(["integrate", "install", "--home", str(home)])
        main(["status", "--project", "x"])


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enter(isolated_home())


class InheritsIsolation(IsolatedTests):
    def test_activate_repair(self):
        main(["activate", "--repair", "--project", "x"])
"""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test_leaky.py").write_text(leaky, encoding="utf-8")
            violations = home_isolation_violations(Path(tmp))

        self.assertEqual(
            [item.split(" ", 1)[1] for item in violations],
            [
                "main(['activate', ...]) outside isolated_home()",
                "activate_project(...) without an explicit home=",
                "install_project(...) without an explicit home=",
                "`-m vesta activate` subprocess outside isolated_home()",
            ],
        )


class RuntimeHomeIsolationTests(unittest.TestCase):
    def _assert_inside(self, path: str | Path, *roots: Path) -> None:
        resolved = Path(path).resolve()
        self.assertNotEqual(resolved, PROCESS_HOME)
        self.assertTrue(
            any(resolved == root or root in resolved.parents for root in roots),
            f"{resolved} is outside {[str(root) for root in roots]}",
        )

    def test_isolated_home_moves_path_home_off_the_process_home(self):
        with isolated_home() as home:
            home = home.resolve()
            self.assertNotEqual(home, PROCESS_HOME)
            self.assertEqual(Path.home().resolve(), home)
            self.assertEqual(Path(os.path.expanduser("~")).resolve(), home)
        self.assertEqual(Path.home().resolve(), PROCESS_HOME)

    def test_activate_repair_cli_installs_globals_only_into_isolated_home(self):
        """The exact path that leaked: `vesta activate --repair`, in-process."""
        real_install = integrations.install_global_integrations
        with (
            isolated_home() as home,
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                integrations, "install_global_integrations", wraps=real_install
            ) as install,
        ):
            home = home.resolve()
            root = Path(tmp).resolve()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["activate", "--repair", "--project", str(root)])
            payload = json.loads(out.getvalue())

            self.assertEqual(code, 0)
            install.assert_called_once()
            self._assert_inside(install.call_args.kwargs["home"], home)
            self._assert_inside(payload["home"], home)
            written = payload["global_integrations"]["written"]
            self.assertTrue(written)
            for path in written:
                self._assert_inside(path, home, root)

    def test_install_project_forwards_explicit_home(self):
        real_bridge = integrations.ensure_superpowers_bridge
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as home_tmp,
            mock.patch.object(
                integrations, "ensure_superpowers_bridge", wraps=real_bridge
            ) as bridge,
        ):
            root = Path(tmp).resolve()
            home = Path(home_tmp).resolve()
            result = installer.install_project(
                root, install_tools=False, install_superpowers=False, home=home
            )

            self._assert_inside(result["activation"]["home"], home)
            self.assertIsNone(result["activation"]["global_integrations"])
            bridge.assert_called_once()
            self._assert_inside(bridge.call_args.args[0], home)


if __name__ == "__main__":
    unittest.main()
