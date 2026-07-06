import unittest

from opaihub.agent_policy import resolve_agent_policy
from opaihub.provider_adapters import (
    adapter_for,
    gemini_approval_mode,
    resolve_execution_plan,
)


class ProviderCapabilityTests(unittest.TestCase):
    def test_editable_free_provider_gets_bounded_repository_tools(self):
        plan = resolve_execution_plan(
            adapter_for("gemini"),
            resolve_agent_policy("Fix the bug and run tests."),
            effective_mode="full-auto",
        )

        self.assertTrue(plan.allow_edits)
        self.assertEqual(plan.mode, "full-auto")
        self.assertEqual(
            plan.tools,
            ("find_files", "search_code", "read_file", "apply_patch", "run_tests"),
        )

    def test_read_only_intent_removes_mutating_tools_in_full_auto(self):
        plan = resolve_execution_plan(
            adapter_for("gemini"),
            resolve_agent_policy("Explain the code. Do not edit files."),
            effective_mode="full-auto",
        )

        self.assertFalse(plan.allow_edits)
        self.assertEqual(plan.mode, "ask")
        self.assertEqual(plan.tools, ("find_files", "search_code", "read_file"))

    def test_gemini_cli_approval_mode_matches_opai_mode(self):
        self.assertEqual(gemini_approval_mode("ask"), "plan")
        self.assertEqual(gemini_approval_mode("plan"), "plan")
        self.assertEqual(gemini_approval_mode("safe-auto"), "auto_edit")
        self.assertEqual(gemini_approval_mode("approve-edits"), "auto_edit")
        self.assertEqual(gemini_approval_mode("full-auto"), "yolo")
        self.assertEqual(gemini_approval_mode("unknown"), "plan")


if __name__ == "__main__":
    unittest.main()
