"""Tests for task focus modes + output formats + prompt composition (Qt-free)."""

from __future__ import annotations

import unittest

from vesta.gui_modes import (
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
from vestahub.gui_preferences import MODES


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


class ParsePlanStepsTests(unittest.TestCase):
    def _parse(self, text):
        from vesta.gui_modes import parse_plan_steps

        return parse_plan_steps(text)

    def test_numbered_steps(self):
        steps = self._parse("Plan:\n1. Add the model\n2. Write tests\n3) Ship it")
        self.assertEqual(steps, ["Add the model", "Write tests", "Ship it"])

    def test_bulleted_steps_and_markdown_stripping(self):
        steps = self._parse("- **Refactor auth**\n* `Run tests`")
        self.assertEqual(steps, ["Refactor auth", "Run tests"])

    def test_prose_is_not_a_plan(self):
        self.assertEqual(self._parse("Just do the thing carefully."), [])

    def test_single_step_is_not_a_plan(self):
        self.assertEqual(self._parse("1. Only one step here"), [])

    def test_empty_and_none_are_safe(self):
        self.assertEqual(self._parse(""), [])
        self.assertEqual(self._parse(None), [])

    def test_steps_are_capped_in_length(self):
        steps = self._parse(f"1. {'x' * 500}\n2. second step")
        self.assertLessEqual(len(steps[0]), 300)


class PipelinePlanPayloadTests(unittest.TestCase):
    def _run(self, mode, answer_text):
        import tempfile
        from pathlib import Path

        from _helpers import FakeStreamingRunner, make_repo
        from vestahub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            return handle_gui_message(
                root,
                "plan the work",
                model_id="account:claude:opus",
                mode=mode,
                account_runner=FakeStreamingRunner(chunks=[answer_text]),
                # Callbacks select the streaming path (as the real GUI does).
                on_event=lambda e: None,
                on_text=lambda t: None,
            )

    def test_plan_mode_attaches_parsed_steps(self):
        result = self._run("plan", "1. First step here\n2. Second step here")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(
            result["plan"]["steps"], ["First step here", "Second step here"]
        )
        self.assertEqual(result["plan"]["source"], "parsed_from_answer")

    def test_non_plan_mode_never_attaches_a_plan(self):
        result = self._run("ask", "1. First step here\n2. Second step here")
        self.assertEqual(result.get("plan") or {}, {})

    def test_prose_plan_answer_attaches_nothing(self):
        result = self._run("plan", "I would start by looking at the code.")
        self.assertEqual(result.get("plan") or {}, {})


if __name__ == "__main__":
    unittest.main()
