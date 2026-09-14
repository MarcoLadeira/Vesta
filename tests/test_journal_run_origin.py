"""#818 AC2: the canonical record has to know which surface asked.

The acceptance criterion is that GUI, CLI and background projections are
generated from *the same canonical state*. That is impossible if the state
cannot tell them apart -- and it could not.

``handle_gui_message`` is the one turn pipeline. Five things call it: the
QtWebEngine desktop, the classic desktop host, ``opai ask`` / ``opai route``,
background automations, and ``opai build``. The admission inside it recorded
``surface="gui"`` as a literal, so every run from every one of those five was
filed in the journal as a desktop run. ``journal_retirement`` already groups
runs by ``origin_surface`` to report populations; that report could only ever
have one row.

These tests pin the value each caller records, and -- more importantly -- that
each caller records one at all. A default that quietly means "gui" would put
the same bug back the first time somebody adds a sixth surface.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from opaihub import gui_pipeline, journal_retirement, journal_runtime, journal_store

NOW = "2026-09-08T10:00:00+00:00"

#: Every module that drives a turn, and the surface it must name.
CALLERS = {
    "opai/gui_web.py": "gui",
    "opai/gui_desktop.py": "gui",
    "opai/cli_stream.py": "cli",
    "opaihub/background_runs.py": "background",
    "opaihub/build_loop.py": "automation",
    "opaihub/objective_worker.py": "agent",
}

ROOT = Path(__file__).resolve().parents[1]


def _pipeline_calls(source: str) -> list[ast.Call]:
    """Every ``handle_gui_message(...)`` call in a module."""

    tree = ast.parse(source)
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else target.id
            if isinstance(target, ast.Name)
            else ""
        )
        if name == "handle_gui_message":
            found.append(node)
    return found


class NormalizationTests(unittest.TestCase):
    def test_known_surfaces_survive_case_and_spacing(self):
        self.assertEqual(gui_pipeline._normalized_surface("CLI"), "cli")
        self.assertEqual(gui_pipeline._normalized_surface(" Background "), "background")

    def test_an_unrecognised_surface_is_named_unknown_not_guessed(self):
        for value in ("ide", "chatops", "slack", "", None, 7):
            with self.subTest(value=value):
                self.assertEqual(gui_pipeline._normalized_surface(value), "unknown")

    def test_the_vocabulary_is_closed(self):
        """Free text would fragment the grouping the column exists for."""

        self.assertEqual(
            set(gui_pipeline.KNOWN_SURFACES),
            {"gui", "cli", "background", "agent", "automation"},
        )

    def test_the_default_does_not_claim_to_be_the_desktop(self):
        """The bug, in its most reusable form.

        A default of "gui" means the next surface someone adds is recorded as
        a desktop run until somebody notices -- which is how this one survived.
        """

        import inspect

        signature = inspect.signature(gui_pipeline._handle_gui_message)
        default = signature.parameters["surface"].default

        self.assertEqual(gui_pipeline._normalized_surface(default), "unknown")


class EveryCallerNamesItselfTests(unittest.TestCase):
    """The wiring, checked structurally rather than by string search.

    This branch has now found seven pieces of #613 machinery that nothing
    called correctly. An AST walk catches a *new* call site too, which a
    hardcoded list of expected strings never would.
    """

    def test_every_pipeline_call_passes_a_surface(self):
        missing = []
        for module in sorted(CALLERS):
            source = (ROOT / module).read_text(encoding="utf-8")
            for call in _pipeline_calls(source):
                names = {kw.arg for kw in call.keywords if kw.arg}
                if "surface" not in names:
                    missing.append(f"{module}:{call.lineno}")

        self.assertEqual(
            missing,
            [],
            "these turns are admitted without saying which surface asked, so "
            "the journal records them as whatever the default happens to be:\n  "
            + "\n  ".join(missing),
        )

    def test_each_caller_names_the_surface_it_actually_is(self):
        wrong = []
        for module, expected in sorted(CALLERS.items()):
            source = (ROOT / module).read_text(encoding="utf-8")
            for call in _pipeline_calls(source):
                for keyword in call.keywords:
                    if keyword.arg != "surface":
                        continue
                    if not isinstance(keyword.value, ast.Constant):
                        continue
                    if keyword.value.value != expected:
                        wrong.append(
                            f"{module}:{call.lineno} says "
                            f"{keyword.value.value!r}, expected {expected!r}"
                        )

        self.assertEqual(wrong, [])

    def test_the_caller_list_has_not_gone_stale(self):
        """A sixth surface must be triaged here, not discovered in a report."""

        found = set()
        for path in sorted((ROOT / "opai").rglob("*.py")) + sorted(
            (ROOT / "opaihub").rglob("*.py")
        ):
            if path.name == "gui_pipeline.py":
                continue
            if _pipeline_calls(path.read_text(encoding="utf-8")):
                found.add(str(path.relative_to(ROOT)).replace("\\", "/"))

        self.assertEqual(
            found,
            set(CALLERS),
            "a module drives the turn pipeline and is not in CALLERS. Add it"
            " there with the surface it is (one of gui_pipeline.KNOWN_SURFACES"
            " -- an agent worker is 'agent'), and pass that as surface= on"
            " each handle_gui_message call, or its turns are journalled as"
            " 'unknown'.",
        )


class WhatTheJournalRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def admit(self, run_id: str, task_id: str, surface: str) -> None:
        journal_runtime.record_admission(
            self.root,
            task_id=task_id,
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface=surface,
        )

    def origin_of(self, task_id: str) -> str:
        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT origin_surface FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        finally:
            store.close()
        return str(row[0]) if row else ""

    def test_a_cli_run_is_recorded_as_a_cli_run(self):
        self.admit("run-1", "task-1", "cli")

        self.assertEqual(self.origin_of("task-1"), "cli")

    def test_surfaces_are_distinguishable_in_the_population_report(self):
        """``journal_retirement`` already grouped by this and always saw one row."""

        self.admit("run-1", "task-1", "gui")
        self.admit("run-2", "task-2", "cli")
        self.admit("run-3", "task-3", "background")
        self.admit("run-4", "task-4", "automation")

        populations = journal_retirement._journal_runs_by_surface(self.root)

        self.assertEqual(
            populations, {"gui": 1, "cli": 1, "background": 1, "automation": 1}
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
