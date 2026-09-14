"""Every model family gets the same standing project instructions.

The defect: Vesta's account models run through their vendor CLIs, which read
`AGENTS.md` / `CLAUDE.md` themselves, while local and free-tier models went
through a prompt built from languages, markers, test commands, and git status —
with the instruction files explicitly excluded from context. So a rule the user
wrote in `AGENTS.md` was honoured by Claude and silently ignored by Gemini, and
nothing about the request explained the difference.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub.project_instructions import (
    DEFAULT_CHAR_BUDGET,
    INSTRUCTION_FILES,
    build_system_prompt,
    load_project_instructions,
)

BASE = "You are Vesta's local-first coding assistant."


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_project_with_no_instructions_contributes_nothing(self) -> None:
        self.assertEqual(load_project_instructions(self.root), "")
        # And the system prompt is left exactly as it was — no placeholder,
        # no invented "the project has no rules" sentence.
        self.assertEqual(build_system_prompt(BASE, self.root), BASE)

    def test_instructions_reach_the_prompt(self) -> None:
        self._write("AGENTS.md", "Always run the tests before claiming done.")
        prompt = build_system_prompt(BASE, self.root)
        self.assertIn("Always run the tests before claiming done.", prompt)
        # Vesta's own rules stay first: the project may direct the work, it does
        # not get to overrule Vesta's safety and honesty rules.
        self.assertLess(prompt.index(BASE), prompt.index("Always run the tests"))

    def test_every_supported_filename_is_read(self) -> None:
        for index, name in enumerate(INSTRUCTION_FILES):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"rule number {index}", encoding="utf-8")
                    self.assertIn(
                        f"rule number {index}", load_project_instructions(root)
                    )

    def test_multiple_files_are_combined_in_a_fixed_order(self) -> None:
        self._write("AGENTS.md", "agents rule")
        self._write("CLAUDE.md", "claude rule")
        first = load_project_instructions(self.root)
        second = load_project_instructions(self.root)
        self.assertIn("agents rule", first)
        self.assertIn("claude rule", first)
        self.assertLess(first.index("agents rule"), first.index("claude rule"))
        # Deterministic: the same repository always produces the same layer.
        self.assertEqual(first, second)

    def test_each_file_is_labelled_so_the_model_knows_the_source(self) -> None:
        self._write("AGENTS.md", "agents rule")
        self.assertIn("--- AGENTS.md ---", load_project_instructions(self.root))

    def test_an_empty_file_is_skipped_rather_than_adding_a_bare_header(self) -> None:
        self._write("AGENTS.md", "   \n\n  ")
        self.assertEqual(load_project_instructions(self.root), "")


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_huge_instruction_file_cannot_swamp_the_context(self) -> None:
        (self.root / "AGENTS.md").write_text(
            "\n".join(f"rule {i}" for i in range(10_000)), encoding="utf-8"
        )
        loaded = load_project_instructions(self.root)
        self.assertLessEqual(len(loaded), DEFAULT_CHAR_BUDGET)

    def test_truncation_is_stated_not_hidden(self) -> None:
        # A silently half-applied rule set is worse than a visibly clipped one.
        (self.root / "AGENTS.md").write_text(
            "\n".join(f"rule {i}" for i in range(10_000)), encoding="utf-8"
        )
        self.assertIn("(truncated)", load_project_instructions(self.root))

    def test_one_file_cannot_starve_the_others(self) -> None:
        (self.root / "AGENTS.md").write_text(
            "\n".join(f"rule {i}" for i in range(10_000)), encoding="utf-8"
        )
        (self.root / "CLAUDE.md").write_text("the claude rule", encoding="utf-8")
        loaded = load_project_instructions(self.root)
        self.assertIn("the claude rule", loaded)

    def test_the_budget_is_honoured_when_caller_supplied(self) -> None:
        (self.root / "AGENTS.md").write_text("x" * 5_000, encoding="utf-8")
        self.assertLessEqual(
            len(load_project_instructions(self.root, char_budget=500)), 500
        )


class RobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_directory_where_a_file_is_expected_is_ignored(self) -> None:
        (self.root / "AGENTS.md").mkdir()
        self.assertEqual(load_project_instructions(self.root), "")

    def test_a_missing_project_root_never_raises(self) -> None:
        self.assertEqual(load_project_instructions(self.root / "nope" / "nowhere"), "")

    def test_undecodable_bytes_do_not_break_the_turn(self) -> None:
        (self.root / "AGENTS.md").write_bytes(b"rule one\n\xff\xfe\nrule two")
        loaded = load_project_instructions(self.root)
        self.assertIn("rule one", loaded)

    def test_a_users_global_config_is_never_read(self) -> None:
        # Only the repository's own files. A personal ~/.claude/CLAUDE.md can
        # describe unrelated work and is not this request's business.
        self.assertNotIn("~", "".join(INSTRUCTION_FILES))
        for name in INSTRUCTION_FILES:
            with self.subTest(name=name):
                self.assertFalse(Path(name).is_absolute())


class AskWiringTests(unittest.TestCase):
    """The layer has to actually reach the runner, not just exist."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "AGENTS.md").write_text(
            "House rule: never touch the database.", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_local_run_receives_the_projects_rules(self) -> None:
        from opaihub.ask import run_ask

        seen: dict[str, str] = {}

        class Runner:
            name = "fake-local"
            model = "fake"

            def available(self) -> bool:
                return True

            def complete(self, prompt: str, *, system: str = "", **_kw) -> str:
                seen["system"] = system
                return "done"

        run_ask(self.root, "explain this repo", runner=Runner(), record=False)
        self.assertIn("House rule: never touch the database.", seen.get("system", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
