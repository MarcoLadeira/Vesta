"""Tests for task focus modes + output formats + prompt composition (Qt-free)."""

from __future__ import annotations

import unittest

from opai.gui_modes import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_TASK_MODE,
    compose_prompt,
    output_format,
    output_formats,
    run_mode_for_task,
    task_mode,
    task_modes,
    task_summary,
)
from opaihub.gui_preferences import MODES


class TaskModeTests(unittest.TestCase):
    def test_default_is_neutral_general(self):
        self.assertEqual(DEFAULT_TASK_MODE, "general")
        self.assertEqual(task_mode("general")["preface"], "")

    def test_modes_present_and_run_modes_valid(self):
        ids = {m["id"] for m in task_modes()}
        for needed in ("general", "build", "debug", "explain", "plan", "review"):
            self.assertIn(needed, ids)
        for mode in task_modes():
            self.assertIn(mode["run_mode"], MODES)

    def test_read_only_modes_map_to_read_only_run_modes(self):
        self.assertEqual(run_mode_for_task("explain"), "ask")
        self.assertEqual(run_mode_for_task("plan"), "plan")
        self.assertEqual(run_mode_for_task("review"), "ask")
        self.assertTrue(task_summary("explain", "normal")["read_only"])
        self.assertFalse(task_summary("build", "normal")["read_only"])

    def test_unknown_mode_falls_back_to_default(self):
        self.assertEqual(task_mode("nope")["id"], DEFAULT_TASK_MODE)


class OutputFormatTests(unittest.TestCase):
    def test_default_normal_has_no_instruction(self):
        self.assertEqual(DEFAULT_OUTPUT_FORMAT, "normal")
        self.assertEqual(output_format("normal")["instruction"], "")

    def test_known_formats_have_instructions(self):
        ids = {f["id"] for f in output_formats()}
        for needed in ("steps", "code", "table", "bug", "json"):
            self.assertIn(needed, ids)
        self.assertTrue(output_format("json")["instruction"])

    def test_unknown_format_falls_back_to_normal(self):
        self.assertEqual(output_format("zzz")["id"], "normal")


class ComposePromptTests(unittest.TestCase):
    def test_defaults_leave_prompt_unchanged(self):
        # The critical regression guard: default General + Normal must not alter
        # the user's text (so existing send behavior is preserved exactly).
        self.assertEqual(compose_prompt("fix the bug"), "fix the bug")
        self.assertEqual(
            compose_prompt("x", task_mode_id="general", output_format_id="normal"), "x"
        )

    def test_task_preface_goes_before_body(self):
        out = compose_prompt("do it", task_mode_id="build")
        self.assertTrue(out.startswith("Implement"))
        self.assertIn("do it", out)
        self.assertLess(out.index("Implement"), out.index("do it"))

    def test_format_instruction_goes_after_body(self):
        out = compose_prompt("explain auth", output_format_id="steps")
        self.assertIn("explain auth", out)
        self.assertIn("step-by-step", out.lower())
        self.assertGreater(out.index("step-by-step"), out.index("explain auth"))

    def test_both_preface_and_instruction(self):
        out = compose_prompt(
            "ship it", task_mode_id="test", output_format_id="checklist"
        )
        lines = out.split("\n\n")
        self.assertEqual(len(lines), 3)
        self.assertIn("ship it", lines[1])

    def test_empty_prompt_is_safe(self):
        self.assertEqual(compose_prompt("   "), "")


if __name__ == "__main__":
    unittest.main()
